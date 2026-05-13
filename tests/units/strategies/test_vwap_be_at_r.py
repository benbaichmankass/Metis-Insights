"""vwap.monitor() ``cfg["be_at_r"]`` threading tests.

Companion to ``test_vwap_monitor_close.py``. Pins the contract added
post-2026-05-13: the SL-to-break-even fallback consults
``cfg["be_at_r"]`` instead of using a hard-coded 1.0R threshold, so
operators can tune via ``config/strategies.yaml`` without touching
source.

Backward compatibility — when ``cfg`` is empty / missing the knob,
the threshold falls back to 1.0R (matching the pre-2026-05-13
behaviour pinned in ``TestSlToBreakeven`` of the close-path test
file). That contract continues to hold; this file only adds tests
for the new tunable behaviour.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from src.units.strategies import vwap


def _candles_with_close(closes, *, volume: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame({
        "open": list(closes),
        "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes],
        "close": list(closes),
        "volume": [volume] * len(closes),
    })


def _pkg(*, direction: str, entry: float, sl: float, tp: float,
         created_at: Optional[str] = None, symbol: str = "BTCUSDT") -> dict:
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    return {
        "order_package_id": "pkg-be-at-r-test",
        "strategy_name": "vwap",
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "created_at": created_at,
        "status": "open",
    }


class TestBeAtRThreshold:
    """Frames are constructed so VWAP-cross / TP-cross / SL-cross /
    time-decay are all dormant — only the SL-to-BE branch is in play.
    Long: entry 100, sl 99 (1R = $1), tp 110. Live vwap > price keeps
    VWAP-cross dormant. TP 110 keeps TP-cross dormant. Fresh
    ``created_at`` keeps time-decay dormant.
    """

    # Live vwap on this frame is ~103.7 (well above any "trigger" close).
    HIGH_VWAP_FRAME_HEAD = [105.0, 105.0]

    def _frame(self, last_close: float) -> pd.DataFrame:
        return _candles_with_close(self.HIGH_VWAP_FRAME_HEAD + [last_close])

    def test_default_threshold_is_1r(self):
        """Empty cfg → BE fires at +1R (entry + 1*risk)."""
        df = self._frame(101.0)  # = entry + 1R
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        assert vwap.monitor({}, df, pkg) == {"sl": 100.0}

    def test_default_below_1r_no_be(self):
        df = self._frame(100.5)  # = entry + 0.5R
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        assert vwap.monitor({}, df, pkg) is None

    def test_half_r_threshold_fires_at_half_r(self):
        """``be_at_r=0.5`` → BE fires at entry + 0.5R = 100.5."""
        df = self._frame(100.5)
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        assert vwap.monitor({"be_at_r": 0.5}, df, pkg) == {"sl": 100.0}

    def test_half_r_threshold_no_be_at_quarter_r(self):
        df = self._frame(100.25)  # = entry + 0.25R, below 0.5R threshold
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        assert vwap.monitor({"be_at_r": 0.5}, df, pkg) is None

    def test_two_r_threshold_no_be_at_1r(self):
        """``be_at_r=2.0`` → +1R is below threshold, no BE move."""
        df = self._frame(101.0)
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        assert vwap.monitor({"be_at_r": 2.0}, df, pkg) is None

    def test_two_r_threshold_fires_at_2r(self):
        df = self._frame(102.0)  # = entry + 2R
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        assert vwap.monitor({"be_at_r": 2.0}, df, pkg) == {"sl": 100.0}

    def test_short_half_r_threshold(self):
        # Short: entry 100, sl 101, tp 90. Low-vwap frame keeps
        # short VWAP-cross dormant.
        df = _candles_with_close([95.0, 95.0, 99.5])  # entry - 0.5R
        pkg = _pkg(direction="short", entry=100.0, sl=101.0, tp=90.0)
        assert vwap.monitor({"be_at_r": 0.5}, df, pkg) == {"sl": 100.0}

    def test_invalid_be_at_r_falls_back_to_default(self):
        df = self._frame(101.0)
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        # String → fallback to 1.0 → fires at +1R.
        assert vwap.monitor({"be_at_r": "bad"}, df, pkg) == {"sl": 100.0}

    def test_zero_be_at_r_falls_back_to_default(self):
        df = self._frame(101.0)
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        assert vwap.monitor({"be_at_r": 0.0}, df, pkg) == {"sl": 100.0}

    def test_negative_be_at_r_falls_back_to_default(self):
        df = self._frame(101.0)
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        assert vwap.monitor({"be_at_r": -0.5}, df, pkg) == {"sl": 100.0}
