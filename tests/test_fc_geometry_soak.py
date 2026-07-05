"""Unit tests for the fc-geometry shadow-soak (M19 D1, observe-only).

Proves ``build_fc_geometry_record`` logs the placed geometry + the live fc
snapshot, degrades honestly when no forecast is served (``fc_present:false``
rather than dropping the row — the coverage denominator), returns ``None``
rather than raising on bad geometry, and that the best-effort writer appends a
JSONL line the reader envelope then surfaces with the coverage summary.
"""
from __future__ import annotations

import json

import pytest

from src.runtime import fc_geometry_soak
from src.runtime.fc_geometry_soak import (
    build_fc_geometry_record,
    read_soak_records,
    record_fc_geometry_soak,
)

FC_ROW = {"fc_range_rel": 0.012, "fc_skew": 0.1, "fc_q10_rel": -0.006,
          "fc_q90_rel": 0.006, "fc_median_rel": 0.0, "fc_up_prob": 0.55}


@pytest.fixture()
def _fc_served(monkeypatch):
    """Serve a fixed fc row through the forecast_live reader."""
    import src.runtime.forecast_live as fl

    monkeypatch.setattr(fl, "compute_live_forecast_row", lambda s, **kw: dict(FC_ROW))
    return FC_ROW


def test_record_carries_placed_geometry_and_fc_snapshot(_fc_served):
    rec = build_fc_geometry_record(
        venue="api", strategy="vwap", symbol="BTCUSDT", direction="long",
        entry=100.0, sl=90.0, tp=115.0, qty=2.0,
        account_id="bybit_2", account_class="real_money", timeframe="15m",
        extra={"side": "Buy", "exchange": "bybit"},
    )
    assert rec is not None
    assert rec["placed"] == {"entry": 100.0, "sl": 90.0, "tp": 115.0, "qty": 2.0,
                             "side": "Buy", "exchange": "bybit"}
    assert rec["fc_present"] is True
    assert rec["fc_row"] == FC_ROW
    assert rec["fc_source"] == "forecast_live"
    assert rec["venue"] == "api"
    assert rec["account_class"] == "real_money"


def test_missing_fc_logs_coverage_row_not_none(monkeypatch):
    import src.runtime.forecast_live as fl

    monkeypatch.setattr(fl, "compute_live_forecast_row", lambda s, **kw: None)
    rec = build_fc_geometry_record(
        venue="api", strategy="vwap", symbol="MES", direction="short",
        entry=5000.0, sl=5050.0, tp=4900.0, qty=1.0,
    )
    assert rec is not None            # the row still counts toward coverage
    assert rec["fc_present"] is False
    assert rec["fc_row"] is None


def test_fc_reader_exception_never_blocks_the_record(monkeypatch):
    import src.runtime.forecast_live as fl

    def _boom(s, **kw):
        raise RuntimeError("reader down")

    monkeypatch.setattr(fl, "compute_live_forecast_row", _boom)
    rec = build_fc_geometry_record(
        venue="api", strategy="vwap", symbol="BTCUSDT", direction="long",
        entry=100.0, sl=90.0, tp=110.0, qty=1.0,
    )
    assert rec is not None
    assert rec["fc_present"] is False


@pytest.mark.parametrize("entry,sl,tp,qty", [
    (0, 90, 110, 1), (100, 0, 110, 1), (100, 90, 0, 1), (100, 90, 110, 0),
    (None, 90, 110, 1), ("garbage", 90, 110, 1),
])
def test_bad_geometry_returns_none_never_raises(entry, sl, tp, qty, _fc_served):
    rec = build_fc_geometry_record(
        venue="api", strategy="s", symbol="BTCUSDT", direction="long",
        entry=entry, sl=sl, tp=tp, qty=qty,
    )
    assert rec is None


def test_writer_appends_and_reader_summarises(tmp_path, monkeypatch, _fc_served):
    log = tmp_path / "fc_geometry_soak.jsonl"
    monkeypatch.setattr(fc_geometry_soak, "soak_log_path", lambda: log)
    # record_fc_geometry_soak resolves through runtime_logs_dir; point it at tmp
    import src.utils.paths as paths

    monkeypatch.setattr(paths, "runtime_logs_dir", lambda: tmp_path)

    rec = record_fc_geometry_soak(
        venue="api", strategy="vwap", symbol="BTCUSDT", direction="long",
        entry=100.0, sl=90.0, tp=110.0, qty=1.5, account_id="bybit_2",
    )
    assert rec is not None
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["symbol"] == "BTCUSDT"

    # add a no-fc row for a second symbol, then read back
    import src.runtime.forecast_live as fl

    monkeypatch.setattr(fl, "compute_live_forecast_row", lambda s, **kw: None)
    record_fc_geometry_soak(
        venue="api", strategy="mgc_pullback_1d", symbol="MGC", direction="short",
        entry=2000.0, sl=2020.0, tp=1950.0, qty=1.0, account_id="ib_paper",
    )

    env = read_soak_records(limit=10)
    assert env["present"] is True
    assert env["summary"]["total_scanned"] == 2
    assert env["summary"]["fc_present"] == 1
    assert env["summary"]["fc_coverage_pct"] == 50.0
    assert env["summary"]["by_symbol"] == {"BTCUSDT": 1, "MGC": 1}
    # newest-first
    assert env["records"][0]["symbol"] == "MGC"

    # filters
    only_fc = read_soak_records(limit=10, fc_only=True)
    assert only_fc["summary"]["total_scanned"] == 1
    assert only_fc["records"][0]["symbol"] == "BTCUSDT"
    by_acct = read_soak_records(limit=10, account_id="ib_paper")
    assert by_acct["summary"]["total_scanned"] == 1


def test_reader_absent_log_is_present_false(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fc_geometry_soak, "soak_log_path", lambda: tmp_path / "nope.jsonl"
    )
    env = read_soak_records()
    assert env == {"present": False, "log_path": str(tmp_path / "nope.jsonl"),
                   "count": 0, "records": [], "summary": {}}
