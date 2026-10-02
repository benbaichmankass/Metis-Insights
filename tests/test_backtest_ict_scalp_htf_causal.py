"""ICT-SCALP-HTF: backtest_ict_scalp's HTF bias series must be causal — a base
bar may only see HTF bars that have fully CLOSED at/before its own open time.
Before the fix the 1h bar was labelled by its open, so the 10:00 row (close of
10:55) was handed to the 10:05 base bar."""
import importlib.util
import sys
from pathlib import Path

import pandas as pd

_P = Path(__file__).resolve().parents[1] / "scripts" / "backtest_ict_scalp.py"
sys.path.insert(0, str(_P.parent))
sys.path.insert(0, str(_P.parents[1]))
_spec = importlib.util.spec_from_file_location("bt_ict_scalp_htf_test", str(_P))
bt = importlib.util.module_from_spec(_spec)
sys.modules["bt_ict_scalp_htf_test"] = bt
_spec.loader.exec_module(bt)


def _feed(n_hours=40):
    ts = pd.date_range("2026-01-01", periods=n_hours * 12, freq="5min", tz="UTC")
    # close encodes the bar index so the leaked value is identifiable
    return pd.DataFrame({"timestamp": ts, "close": range(len(ts))})


def test_htf_rows_are_stamped_at_close_and_never_future():
    df = _feed()
    htf = bt._build_htf_series(df, htf_rule="1h", ema_period=5)
    aligned = pd.merge_asof(df[["timestamp"]], htf, on="timestamp", direction="backward")
    # For every base bar, the HTF close it sees must come from a base bar whose
    # own close time (open + 5m) is <= this bar's open time + 5m ... i.e. the
    # HTF bar closed no later than this bar opened.
    seen = aligned.dropna(subset=["htf_close"])
    # htf_close value == index of the last 5m bar of the closed hour; that bar
    # opened 5m before the hour boundary, so index*5min + 5min <= bar open time.
    base_open = (seen["timestamp"] - df["timestamp"].iloc[0]) / pd.Timedelta("5min")
    assert ((seen["htf_close"] + 1) <= base_open + 1e-9).all()


def test_first_hour_close_not_visible_inside_that_hour():
    df = _feed()
    htf = bt._build_htf_series(df, htf_rule="1h", ema_period=5)
    aligned = pd.merge_asof(df[["timestamp"]], htf, on="timestamp", direction="backward")
    inside_first_hour = aligned.iloc[:12]
    assert inside_first_hour["htf_close"].isna().all()
