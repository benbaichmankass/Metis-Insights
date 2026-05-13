"""turtle_soup.monitor() TP1 partial + TP2 progression + SL-cross tests.

Pins the close-path priority that was unwired pre-2026-05-13:

  1. SL-cross → full close.
  2. TP1-cross while still on the TP1 target → partial close
     ``close_qty_pct == cfg.partial_close_pct`` (default 0.25) with
     ``next_tp == meta.tp2`` so the order_monitor loop rolls the
     package's ``tp`` field forward to TP2.
  3. TP-cross when already on the TP2 runner → full close
     (``reason="tp2_cross"``).
  4. Fallback → ``_base.monitor_breakeven_sl`` with the threshold
     taken from ``cfg.be_at_r``.

The existing TestTurtleSoupMonitor regression in
``tests/test_s030_pr2_strategy_monitor_hook.py`` (empty cfg, no meta)
must still pass — those checks pin backward-compat for legacy package
rows that pre-date the meta.tp2 key.
"""
from __future__ import annotations

import pandas as pd

from src.units.strategies import turtle_soup


def _candles(*closes):
    return pd.DataFrame({
        "open": closes,
        "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes],
        "close": list(closes),
        "volume": [100.0] * len(closes),
    })


# Long: entry 100, sl 98, tp (TP1) 102, meta.tp2 = 106. 1R = $2.
LONG_PKG_TP1 = {
    "entry": 100.0,
    "sl": 98.0,
    "tp": 102.0,
    "direction": "long",
    "symbol": "BTCUSDT",
    "meta": {"tp2": 106.0, "risk_per_unit": 2.0},
}

# Long: same trade after TP1 partial has rolled tp forward to TP2.
LONG_PKG_TP2 = {
    **LONG_PKG_TP1,
    "tp": 106.0,
    "sl": 100.0,  # SL moved to BE on the same earlier tick (typical)
}

# Short: entry 100, sl 102, tp (TP1) 98, meta.tp2 = 94.
SHORT_PKG_TP1 = {
    "entry": 100.0,
    "sl": 102.0,
    "tp": 98.0,
    "direction": "short",
    "symbol": "BTCUSDT",
    "meta": {"tp2": 94.0, "risk_per_unit": 2.0},
}


class TestTurtleSoupTP1Partial:
    def test_long_tp1_cross_emits_partial_with_next_tp(self):
        df = _candles(101.0, 101.5, 102.0)  # price just touches TP1
        verdict = turtle_soup.monitor({}, df, LONG_PKG_TP1)
        assert verdict is not None
        assert verdict["action"] == "close"
        assert verdict["close_qty_pct"] == 0.25
        assert verdict["reason"] == "tp1_partial"
        assert verdict["next_tp"] == 106.0
        assert verdict["exit_price"] == 102.0

    def test_long_tp1_cross_respects_cfg_partial_pct(self):
        df = _candles(102.5)
        verdict = turtle_soup.monitor(
            {"partial_close_pct": 0.4}, df, LONG_PKG_TP1,
        )
        assert verdict["close_qty_pct"] == 0.4

    def test_short_tp1_cross_emits_partial_with_next_tp(self):
        df = _candles(99.0, 98.5, 98.0)
        verdict = turtle_soup.monitor({}, df, SHORT_PKG_TP1)
        assert verdict is not None
        assert verdict["action"] == "close"
        assert verdict["close_qty_pct"] == 0.25
        assert verdict["reason"] == "tp1_partial"
        assert verdict["next_tp"] == 94.0


class TestTurtleSoupTP2FullClose:
    def test_long_tp2_cross_emits_full_close(self):
        df = _candles(105.0, 105.5, 106.0)
        verdict = turtle_soup.monitor({}, df, LONG_PKG_TP2)
        assert verdict == {
            "action": "close",
            "reason": "tp2_cross",
            "exit_price": 106.0,
        }

    def test_legacy_no_tp2_treats_tp_as_full_close(self):
        """A package row from before the meta.tp2 era falls back to a
        single TP-cross full close — no partial path attempted."""
        pkg = {**LONG_PKG_TP1, "meta": {}}
        df = _candles(102.0)
        verdict = turtle_soup.monitor({}, df, pkg)
        assert verdict == {
            "action": "close",
            "reason": "tp_cross",
            "exit_price": 102.0,
        }


class TestTurtleSoupSLCross:
    def test_long_sl_cross_emits_full_close(self):
        df = _candles(99.0, 98.5, 98.0)
        verdict = turtle_soup.monitor({}, df, LONG_PKG_TP1)
        assert verdict == {
            "action": "close",
            "reason": "sl_cross",
            "exit_price": 98.0,
        }

    def test_short_sl_cross_emits_full_close(self):
        df = _candles(101.0, 101.5, 102.0)
        verdict = turtle_soup.monitor({}, df, SHORT_PKG_TP1)
        assert verdict == {
            "action": "close",
            "reason": "sl_cross",
            "exit_price": 102.0,
        }


class TestTurtleSoupBreakEvenThreshold:
    def test_be_at_r_threshold_from_cfg(self):
        """cfg.be_at_r=0.75 should trigger BE at 100 + 0.75*2 = 101.5."""
        pkg = {
            "entry": 100.0, "sl": 98.0, "tp": 102.0, "direction": "long",
            "symbol": "BTCUSDT",
            # No meta → TP1-partial path skipped (tp2 absent). But the
            # TP-cross check still fires at price >= 102 — so feed a
            # candle below TP1 to exercise the BE-only fallthrough.
            "meta": {},
        }
        df = _candles(101.5)
        verdict = turtle_soup.monitor({"be_at_r": 0.75}, df, pkg)
        assert verdict == {"sl": 100.0}

    def test_default_be_at_r_is_1r(self):
        """Empty cfg falls back to be_at_r=1.0 (preserves the original
        TestTurtleSoupMonitor.test_short_one_r_reached_returns_breakeven
        contract in tests/test_s030_pr2_strategy_monitor_hook.py)."""
        pkg = {
            "entry": 100.0, "sl": 102.0, "tp": 96.0, "direction": "short",
            "symbol": "BTCUSDT",
            "meta": {},
        }
        df = _candles(99.0, 98.0)
        # Price 98 = entry - 1R; threshold 1.0 → BE move.
        assert turtle_soup.monitor({}, df, pkg) == {"sl": 100.0}


class TestTurtleSoupDefensive:
    def test_none_candles_returns_none(self):
        assert turtle_soup.monitor({}, None, LONG_PKG_TP1) is None

    def test_empty_candles_returns_none(self):
        assert turtle_soup.monitor({}, pd.DataFrame(), LONG_PKG_TP1) is None

    def test_missing_pkg_keys_returns_none(self):
        df = _candles(102.0)
        assert turtle_soup.monitor({}, df, {"entry": 100.0}) is None

    def test_meta_as_json_string_is_parsed(self):
        """Defensive: a raw row from sqlite carries meta as a JSON
        string. The monitor must parse it so meta.tp2 is honoured."""
        import json
        pkg = {**LONG_PKG_TP1, "meta": json.dumps(LONG_PKG_TP1["meta"])}
        df = _candles(102.0)
        verdict = turtle_soup.monitor({}, df, pkg)
        assert verdict["reason"] == "tp1_partial"
        assert verdict["next_tp"] == 106.0
