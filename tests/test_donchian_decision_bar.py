"""decision_bar: closed|forming for the donchian variant builders
(PI-20260930-QZSE4AMA-0002). Closed = the frame Stage 0 scores."""
import pandas as pd
import pytest

from src.runtime.strategy_signal_builders import _decision_frame

TF = "4h"
H4 = 4 * 3600
T0 = 1_800_000_000 - (1_800_000_000 % H4)  # a 4h boundary (epoch s)


def _frame(n=5, last_open=None):
    last_open = T0 + 10 * H4 if last_open is None else last_open
    ts = [last_open - (n - 1 - i) * H4 for i in range(n)]
    return pd.DataFrame({"timestamp": pd.to_datetime(ts, unit="s", utc=True),
                         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0})


def test_forming_default_is_unchanged():
    df = _frame()
    out, skip = _decision_frame(df, TF, {}, now=T0 + 10 * H4 + 100)
    assert out is df and skip is None


def test_closed_drops_forming_bar_and_evaluates_fresh_close():
    df = _frame(last_open=T0 + 10 * H4)  # bar opened at T0+10H4; forming
    now = T0 + 10 * H4 + 120             # 2 min after the PREVIOUS bar's... 
    out, skip = _decision_frame(df, TF, {"decision_bar": "closed"}, now=now)
    assert len(out) == len(df) - 1 and skip is None
    assert int(out["timestamp"].iloc[-1].timestamp()) == T0 + 9 * H4


def test_closed_skips_once_stale():
    df = _frame(last_open=T0 + 10 * H4)
    now = T0 + 10 * H4 + 3600  # closed bar closed 1h ago
    _, skip = _decision_frame(df, TF, {"decision_bar": "closed"}, now=now)
    assert skip == "closed_bar_already_evaluated"


def test_fresh_window_is_configurable():
    df = _frame(last_open=T0 + 10 * H4)
    now = T0 + 10 * H4 + 3600
    _, skip = _decision_frame(
        df, TF, {"decision_bar": "closed", "decision_bar_fresh_seconds": 7200}, now=now)
    assert skip is None


def test_bad_mode_raises():
    with pytest.raises(ValueError):
        _decision_frame(_frame(), TF, {"decision_bar": "nope"})
