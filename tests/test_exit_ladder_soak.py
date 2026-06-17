"""Unit tests for the venue-agnostic exit-ladder soak (P3, observe-only).

Proves ``build_exit_ladder_record`` produces a single-target-vs-ladder comparison
for both API and prop venues, splits the real order qty across the ladder, and
(like its P1/P2 siblings) returns ``None`` rather than raising on bad input. Also
checks the best-effort writer appends a JSONL line.
"""
from __future__ import annotations

import json

import pytest

from src.runtime import exit_ladder_soak
from src.runtime.exit_ladder_soak import (
    build_exit_ladder_record,
    record_exit_ladder_soak,
)


# --------------------------------------------------------------------------- #
# API venue
# --------------------------------------------------------------------------- #

def test_api_single_target_no_ladder():
    rec = build_exit_ladder_record(
        venue="api", strategy="vwap", symbol="BTCUSDT", direction="long",
        entry=100.0, sl=90.0, tp=110.0, qty=2.0,
        account_id="bybit_2", account_class="real_money",
        extra={"side": "Buy", "exchange": "bybit"},
    )
    assert rec is not None
    assert rec["venue"] == "api"
    assert rec["single_target"] == {
        "entry": 100.0, "sl": 90.0, "tp": 110.0, "qty": 2.0,
        "side": "Buy", "exchange": "bybit",
    }
    # no tp2 → single fixed target, no rungs
    assert rec["ladder"]["n_rungs"] == 0
    assert rec["ladder"]["n_targets"] == 1
    assert rec["ladder"]["targets"][0]["qty"] == 2.0       # full qty on the one target
    assert rec["differs_from_single_target"] is False


def test_api_turtle_ladder_splits_qty_across_rungs():
    rec = build_exit_ladder_record(
        venue="api", strategy="turtle_soup", symbol="BTCUSDT", direction="long",
        entry=100.0, sl=90.0, tp=110.0, qty=4.0,
        order_meta={"tp2": 120.0},          # → TP1 rung + TP2 final
        account_id="bybit_2", account_class="real_money",
    )
    assert rec is not None
    ladder = rec["ladder"]
    assert ladder["n_rungs"] == 1
    assert [t["price"] for t in ladder["targets"]] == [110.0, 120.0]
    # 25% of 4.0 banks at TP1, 75% rides to TP2
    assert ladder["targets"][0]["qty"] == pytest.approx(1.0)
    assert ladder["targets"][1]["qty"] == pytest.approx(3.0)
    assert ladder["stop"] == {"price": 90.0, "qty": 4.0}
    assert rec["differs_from_single_target"] is True


def test_api_short_direction_targets_below_entry():
    rec = build_exit_ladder_record(
        venue="api", strategy="vwap", symbol="BTCUSDT", direction="short",
        entry=100.0, sl=110.0, tp=90.0, qty=1.0,
    )
    assert rec is not None
    assert rec["ladder"]["targets"][0]["price"] == 90.0


# --------------------------------------------------------------------------- #
# Prop venue
# --------------------------------------------------------------------------- #

def test_prop_venue_carries_extra_fields():
    rec = build_exit_ladder_record(
        venue="prop", strategy="trend_donchian", symbol="MES", direction="long",
        entry=5000.0, sl=4980.0, tp=5040.0, qty=3.0,
        account_id="breakout_1", account_class="prop", timeframe="2h",
        extra={"side": "Buy", "rr": 2.0, "qty_units": 3.0},
    )
    assert rec is not None
    assert rec["venue"] == "prop"
    assert rec["timeframe"] == "2h"
    assert rec["single_target"]["rr"] == 2.0
    assert rec["single_target"]["qty_units"] == 3.0


# --------------------------------------------------------------------------- #
# Rejection / robustness
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("kw", [
    {"entry": 0.0, "sl": 90.0, "tp": 110.0, "qty": 1.0},   # non-positive entry
    {"entry": 100.0, "sl": 90.0, "tp": 110.0, "qty": 0.0}, # zero qty
    {"entry": 100.0, "sl": 0.0, "tp": 110.0, "qty": 1.0},  # missing sl
    {"entry": 100.0, "sl": 100.0, "tp": 110.0, "qty": 1.0},# zero risk (entry==sl)
])
def test_degenerate_inputs_return_none(kw):
    rec = build_exit_ladder_record(
        venue="api", strategy="x", symbol="BTCUSDT", direction="long", **kw,
    )
    assert rec is None


@pytest.mark.parametrize("garbage", [
    {"entry": "nope", "sl": 90.0, "tp": 110.0, "qty": 1.0},
    {"entry": None, "sl": None, "tp": None, "qty": None},
])
def test_never_raises_on_garbage(garbage):
    assert build_exit_ladder_record(
        venue="api", strategy="x", symbol="S", direction="long", **garbage,
    ) is None


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #

def test_record_writes_jsonl_line(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.utils.paths.runtime_logs_dir", lambda: tmp_path, raising=True,
    )
    rec = record_exit_ladder_soak(
        venue="api", strategy="turtle_soup", symbol="BTCUSDT", direction="long",
        entry=100.0, sl=90.0, tp=110.0, qty=4.0, order_meta={"tp2": 120.0},
    )
    assert rec is not None
    log = tmp_path / exit_ladder_soak.SOAK_LOG_NAME
    assert log.exists()
    line = json.loads(log.read_text().strip())
    assert line["venue"] == "api"
    assert line["ladder"]["n_rungs"] == 1


def test_record_returns_none_and_writes_nothing_on_bad_input(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.utils.paths.runtime_logs_dir", lambda: tmp_path, raising=True,
    )
    rec = record_exit_ladder_soak(
        venue="api", strategy="x", symbol="S", direction="long",
        entry=0.0, sl=90.0, tp=110.0, qty=1.0,
    )
    assert rec is None
    assert not (tmp_path / exit_ladder_soak.SOAK_LOG_NAME).exists()


# --------------------------------------------------------------------------- #
# Read path (backs /api/bot/exit-ladder/soak)
# --------------------------------------------------------------------------- #

def _seed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.utils.paths.runtime_logs_dir", lambda: tmp_path, raising=True,
    )
    # 2 api turtle (differing), 1 api vwap (parity), 1 prop
    record_exit_ladder_soak(venue="api", strategy="turtle_soup", symbol="BTCUSDT",
                            direction="long", entry=100, sl=90, tp=110, qty=4.0,
                            order_meta={"tp2": 120.0}, account_id="bybit_2")
    record_exit_ladder_soak(venue="api", strategy="turtle_soup", symbol="BTCUSDT",
                            direction="long", entry=100, sl=90, tp=110, qty=2.0,
                            order_meta={"tp2": 120.0}, account_id="bybit_2")
    record_exit_ladder_soak(venue="api", strategy="vwap", symbol="BTCUSDT",
                            direction="long", entry=100, sl=90, tp=110, qty=1.0,
                            account_id="bybit_1")
    record_exit_ladder_soak(venue="prop", strategy="turtle_soup", symbol="MES",
                            direction="long", entry=5000, sl=4980, tp=5040, qty=3.0,
                            order_meta={"tp2": 5080.0}, account_id="breakout_1")


def test_read_returns_present_false_before_any_write(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir", lambda: tmp_path, raising=True)
    out = exit_ladder_soak.read_soak_records()
    assert out["present"] is False
    assert out["count"] == 0


def test_read_newest_first_with_summary(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    out = exit_ladder_soak.read_soak_records()
    assert out["present"] is True
    assert out["count"] == 4
    # newest-first: the prop record was written last
    assert out["records"][0]["venue"] == "prop"
    assert out["summary"]["total_scanned"] == 4
    assert out["summary"]["by_venue"] == {"api": 3, "prop": 1}
    assert out["summary"]["differing"] == 3       # 2 api turtle + 1 prop
    assert out["summary"]["differing_pct"] == 75.0


def test_read_venue_filter(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    out = exit_ladder_soak.read_soak_records(venue="api")
    assert {r["venue"] for r in out["records"]} == {"api"}
    assert out["summary"]["by_venue"] == {"api": 3}


def test_read_account_and_differing_filters(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    out = exit_ladder_soak.read_soak_records(account_id="bybit_2", only_differing=True)
    assert out["count"] == 2
    assert all(r["account_id"] == "bybit_2" for r in out["records"])
    assert all(r["differs_from_single_target"] for r in out["records"])


def test_read_limit_caps_records_but_summary_scans_all(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    out = exit_ladder_soak.read_soak_records(limit=1)
    assert out["count"] == 1                      # page capped
    assert out["summary"]["total_scanned"] == 4   # summary over all
