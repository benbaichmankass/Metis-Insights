"""Tests for live TSFM forecast-feature serving (M19 Track-1 PR 1b).

The live serve reader is stdlib + json only — it runs NO forecaster and imports
NO torch/pandas/numpy/chronos. These tests prove: the reader returns the
published ``fc_row``; a missing artifact / kill switch degrades to ``None``
(never a fabricated zero vector); ``head_wants_forecast`` detects the ``fc_*``
feature list; the module imports with no heavy deps present; and — the parity at
the serve boundary — a row written by the PR-1a producer (``write_forecast_artifact``,
deterministic stub forecaster) reads back byte-for-byte via
``compute_live_forecast_row``.
"""
from __future__ import annotations

import importlib
import json
import os
from datetime import datetime, timedelta

import scripts.ml.publish_live_forecasts as producer
from ml.datasets.forecast_features import FORECAST_FEATURE_COLUMNS

fl = importlib.import_module("src.runtime.forecast_live")


# --------------------------------------------------------------------------- #
# helpers (mirror the PR-1a producer test's deterministic stub + candles)
# --------------------------------------------------------------------------- #
def _stub_forecast():
    """Deterministic batch forecaster — no torch, fully reproducible."""

    def _forecast(windows, horizon, quantile_levels):
        out = []
        for w in windows:
            last = float(w[-1]) if w else 1.0
            out.append({
                quantile_levels[0]: last * 0.997,
                quantile_levels[1]: last * 1.002,
                quantile_levels[2]: last * 1.006,
            })
        return out

    return _forecast


def _candles(n: int, *, base_close: float = 100.0) -> list[dict]:
    base = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    return [
        {
            "timestamp": (base + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"),
            "open": base_close + i,
            "high": (base_close + i) * 1.001,
            "low": (base_close + i) * 0.999,
            "close": base_close + i,
            "volume": 100.0,
        }
        for i in range(n)
    ]


def _point_dir(fl_mod, monkeypatch, tmp_path):
    """Point the reader's forecasts dir at ``tmp_path`` + clear its cache."""
    monkeypatch.setattr(fl_mod, "_forecasts_dir", lambda: str(tmp_path))
    fl_mod._artifact_cache.clear()


# --------------------------------------------------------------------------- #
# reader behaviour
# --------------------------------------------------------------------------- #
def test_reader_returns_fc_row_from_written_artifact(monkeypatch, tmp_path):
    _point_dir(fl, monkeypatch, tmp_path)
    fc_row = {c: float(i) for i, c in enumerate(FORECAST_FEATURE_COLUMNS)}
    (tmp_path / "BTCUSDT.json").write_text(
        json.dumps({"symbol": "BTCUSDT", "timeframe": "15m", "fc_row": fc_row})
    )
    row = fl.compute_live_forecast_row("BTCUSDT")
    assert row is not None
    assert set(row) == set(FORECAST_FEATURE_COLUMNS)
    for c in FORECAST_FEATURE_COLUMNS:
        assert row[c] == fc_row[c] and isinstance(row[c], float)


def test_missing_file_returns_none(monkeypatch, tmp_path):
    _point_dir(fl, monkeypatch, tmp_path)
    assert fl.compute_live_forecast_row("NOPE") is None


def test_kill_switch_returns_none(monkeypatch, tmp_path):
    _point_dir(fl, monkeypatch, tmp_path)
    (tmp_path / "BTCUSDT.json").write_text(
        json.dumps({"timeframe": "15m",
                    "fc_row": {c: 1.0 for c in FORECAST_FEATURE_COLUMNS}})
    )
    monkeypatch.setenv("FORECAST_LIVE_DISABLED", "1")
    assert fl.forecast_live_disabled() is True
    assert fl.compute_live_forecast_row("BTCUSDT") is None


def test_timeframe_mismatch_is_parity_guarded(monkeypatch, tmp_path):
    _point_dir(fl, monkeypatch, tmp_path)
    (tmp_path / "BTCUSDT.json").write_text(
        json.dumps({"timeframe": "15m",
                    "fc_row": {c: 1.0 for c in FORECAST_FEATURE_COLUMNS}})
    )
    # A head scored on a DIFFERENT cadence must not be fed this forecast.
    assert fl.compute_live_forecast_row("BTCUSDT", timeframe="1h") is None
    # A matching cadence (or no cadence requested) is served.
    assert fl.compute_live_forecast_row("BTCUSDT", timeframe="15m") is not None
    fl._artifact_cache.clear()
    assert fl.compute_live_forecast_row("BTCUSDT") is not None


def test_malformed_artifact_returns_none(monkeypatch, tmp_path):
    _point_dir(fl, monkeypatch, tmp_path)
    (tmp_path / "BTCUSDT.json").write_text("{ not json")
    assert fl.compute_live_forecast_row("BTCUSDT") is None
    fl._artifact_cache.clear()
    # A dict with no fc_row → None (never a fabricated zero vector).
    (tmp_path / "ETHUSDT.json").write_text(json.dumps({"timeframe": "15m"}))
    assert fl.compute_live_forecast_row("ETHUSDT") is None


# --------------------------------------------------------------------------- #
# head_wants_forecast / group_needs_forecast gate
# --------------------------------------------------------------------------- #
def test_head_wants_forecast_detection():
    assert fl.head_wants_forecast(["vol_bucket", "fc_ret_med", "log_return"]) is True
    assert fl.head_wants_forecast(["vol_bucket", "log_return"]) is False
    assert fl.head_wants_forecast([]) is False
    assert fl.head_wants_forecast(None) is False


class _Wrapped:
    def __init__(self, cols):
        self._feature_columns = cols


class _Pred:
    def __init__(self, cols):
        self._wrapped = _Wrapped(cols)


def test_group_needs_forecast():
    fc_head = _Pred(["fc_q90_rel", "vol_bucket"])
    plain = _Pred(["vol_bucket", "log_return"])
    assert fl.group_needs_forecast([plain, fc_head]) is True
    assert fl.group_needs_forecast([plain]) is False
    assert fl.group_needs_forecast([object()]) is False  # fail-permissive unwrap


# --------------------------------------------------------------------------- #
# import discipline — no heavy deps in the live import chain
# --------------------------------------------------------------------------- #
def test_module_imported_without_torch_or_pandas():
    # A FRESH interpreter (subprocess) is the honest isolated check — asserting
    # against the shared pytest-process sys.modules would falsely fail whenever a
    # sibling test already imported pandas. Importing the live serve module in a
    # clean process must pull in NONE of the heavy stack.
    import subprocess
    import sys

    code = (
        "import sys; import src.runtime.forecast_live as f;"
        "assert f.compute_live_forecast_row;"
        "leaked=[m for m in ('torch','chronos','pandas','numpy') if m in sys.modules];"
        "assert not leaked, leaked; print('ok')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


# --------------------------------------------------------------------------- #
# end-to-end: produce (stub forecaster) → serve → assert parity at the boundary
# --------------------------------------------------------------------------- #
def test_end_to_end_produce_then_serve_matches(monkeypatch, tmp_path):
    _point_dir(fl, monkeypatch, tmp_path)
    candles = _candles(80)
    path = producer.write_forecast_artifact(
        tmp_path, "BTCUSDT", "15m", candles, forecast_fn=_stub_forecast(),
    )
    assert path is not None and path.exists()

    # What the producer wrote (the parity reference).
    written = json.loads(path.read_text())
    expected = {c: float(written["fc_row"][c]) for c in FORECAST_FEATURE_COLUMNS}

    # What the live reader serves for the same symbol/cadence.
    served = fl.compute_live_forecast_row("BTCUSDT", timeframe="15m")
    assert served == expected  # bit-for-bit parity at the serve boundary


# --------------------------------------------------------------------------- #
# FIX-CA-24: a forecast that is not for the scored bar is not served
# CA-B01-fc-live-no-staleness-check (PI-20260927-3WM5HADW-0007)
# --------------------------------------------------------------------------- #
_BAR = datetime.fromisoformat("2026-09-27T12:00:00+00:00")


def _write_fc(tmp_path, as_of):
    (tmp_path / "BTCUSDT.json").write_text(json.dumps({
        "symbol": "BTCUSDT", "timeframe": "15m", "as_of_ts": as_of,
        "fc_row": {c: 1.0 for c in FORECAST_FEATURE_COLUMNS},
    }))


def _stale_setup(monkeypatch, tmp_path):
    _point_dir(fl, monkeypatch, tmp_path)
    monkeypatch.setattr(fl, "_status_path", lambda: str(tmp_path / "status.json"))
    fl.reset_serve_counters()


def test_stale_artifact_returns_none_and_counts(monkeypatch, tmp_path):
    _stale_setup(monkeypatch, tmp_path)
    # The producer stopped 10 days ago: its last artifact is frozen.
    _write_fc(tmp_path, str(int((_BAR - timedelta(days=10)).timestamp() * 1000)))
    bar_ts = int(_BAR.timestamp() * 1000)
    assert fl.compute_live_forecast_row("BTCUSDT", timeframe="15m", bar_ts=bar_ts) is None
    assert fl.serve_counters()["fc_stale"] == 1
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["symbols"]["BTCUSDT"]["served"] is False
    assert status["symbols"]["BTCUSDT"]["lag_bars"] == 960
    assert status["counters"]["fc_stale"] == 1


def test_forecast_for_scored_bar_is_served(monkeypatch, tmp_path):
    _stale_setup(monkeypatch, tmp_path)
    _write_fc(tmp_path, _BAR.isoformat())
    row = fl.compute_live_forecast_row(
        "BTCUSDT", timeframe="15m", bar_ts=int(_BAR.timestamp() * 1000),
    )
    assert row is not None
    assert fl.serve_counters() == {"fc_served": 1, "fc_stale": 0}


def test_one_bar_publication_lag_is_served(monkeypatch, tmp_path):
    """The producer publishes a bar's forecast after the bar closes, and the
    mirror rsyncs it up to 2 min later; the scorer scores at the close. The
    previous bar's forecast is the in-flight case, not a frozen producer."""
    _stale_setup(monkeypatch, tmp_path)
    _write_fc(tmp_path, (_BAR - timedelta(minutes=15)).isoformat())
    assert fl.compute_live_forecast_row(
        "BTCUSDT", timeframe="15m", bar_ts=_BAR.isoformat(),
    ) is not None
    _write_fc(tmp_path, (_BAR - timedelta(minutes=30)).isoformat())
    fl._artifact_cache.clear()
    assert fl.compute_live_forecast_row(
        "BTCUSDT", timeframe="15m", bar_ts=_BAR.isoformat(),
    ) is None


def test_forecast_ahead_of_scored_bar_is_not_served(monkeypatch, tmp_path):
    """A forecast conditioned on a bar AFTER the scored one is lookahead."""
    _stale_setup(monkeypatch, tmp_path)
    _write_fc(tmp_path, (_BAR + timedelta(minutes=15)).isoformat())
    assert fl.compute_live_forecast_row(
        "BTCUSDT", timeframe="15m", bar_ts=_BAR.isoformat(),
    ) is None


def test_missing_as_of_ts_is_stale_when_bar_given(monkeypatch, tmp_path):
    _stale_setup(monkeypatch, tmp_path)
    (tmp_path / "BTCUSDT.json").write_text(json.dumps({
        "timeframe": "15m", "fc_row": {c: 1.0 for c in FORECAST_FEATURE_COLUMNS},
    }))
    assert fl.compute_live_forecast_row(
        "BTCUSDT", timeframe="15m", bar_ts=_BAR.isoformat(),
    ) is None
