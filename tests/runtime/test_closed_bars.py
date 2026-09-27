"""Tests for src.runtime.closed_bars — the shared forming-bar trim
(FIX-CA-23 / FIX-CA-25)."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from src.runtime.closed_bars import drop_forming_bar, last_bar_is_forming

NOW = 1_800_000_000.0  # bar-aligned for 15m (divisible by 900)


def _df(last_open_s: float, n: int = 5, tf_s: int = 900, as_="ms"):
    opens = [last_open_s - tf_s * (n - 1 - i) for i in range(n)]
    if as_ == "ms":
        ts = [int(o * 1000) for o in opens]
    elif as_ == "dt":
        ts = pd.to_datetime(opens, unit="s", utc=True)
    else:
        ts = [datetime.fromtimestamp(o, tz=timezone.utc).isoformat() for o in opens]
    return pd.DataFrame({"timestamp": ts, "close": range(n)})


def test_forming_bar_dropped_for_each_timestamp_encoding():
    for enc in ("ms", "dt", "iso"):
        df = _df(NOW - 60, as_=enc)  # opened 60 s ago on a 15m tf → forming
        out = drop_forming_bar(df, "15m", now=NOW)
        assert len(out) == len(df) - 1, enc
        assert list(out["close"]) == [0, 1, 2, 3]


def test_closed_bar_kept():
    df = _df(NOW - 900)  # opened exactly one bar ago → closed at NOW
    assert not last_bar_is_forming(df, "15m", now=NOW)
    assert drop_forming_bar(df, "15m", now=NOW) is df


def test_unknown_tf_or_unreadable_ts_is_a_noop():
    df = _df(NOW - 60)
    assert drop_forming_bar(df, "7m", now=NOW) is df
    bad = pd.DataFrame({"timestamp": ["not-a-time"], "close": [1.0]})
    assert drop_forming_bar(bad, "15m", now=NOW) is bad
    assert drop_forming_bar(None, "15m", now=NOW) is None
