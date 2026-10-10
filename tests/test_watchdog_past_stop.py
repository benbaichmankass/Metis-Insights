"""WATCHDOG-PAST-STOP — two bybit_2 (real money) stop-loss closes booked as
``stuck_strategy_watchdog`` and priced off a candle.

MEASURED 2026-10-10 (diag #17448, #17451, #17454; journal rows + the venue fills
store):

* trade 6549 ADAUSDT short 406: journal entry 0.2394, venue entry fill 0.2391.
  Stop 0.24767143. The venue closed it with ONE buy of 406 @ 0.2477 at
  2026-10-10T01:32:08 on order ``e3bf5ffb…`` — the trade's own ``sl_order_id``.
  The journal booked exit 0.2489 (``candle_at_close`` at the watchdog's
  01:34:33 detection time) and labelled it ``stuck_strategy_watchdog``.
* trade 6528 XRPUSDT short 56.9: journal entry 1.3464, venue entry fill 1.3478.
  Closed by a buy of 56.9 @ 1.4024 at 2026-10-09T06:40:00 on order
  ``a189d840…`` — its own ``sl_order_id``. Booked 1.4011 off a candle at 06:42:22.

Both venue stops rested and fired. The broker closed-pnl record was unmatchable
because the matcher compared the venue's ``avgEntryPrice`` with the journal's
INTENDED entry, 12.5 / 10.4 bps away against a 10 bps tolerance.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from src.runtime import order_monitor as om
from src.units.accounts import clients

from tests.test_exit_cluster_flat_reconciled_relabel import (
    _ago,
    _fill,
    _run_bracket_sweep,
)

_ADA_SL_OID = "e3bf5ffb-3709-474d-b3f5-e725e8cee2b9"
_ADA_ENTRY_OID = "30eb3acc-ea2e-474a-8cc9-62684c0c6c5e"
_XRP_SL_OID = "a189d840-4181-466a-b80b-be5665103065"


# ── The matcher, with the real record ────────────────────────────────────────

class _Client:
    def __init__(self, records):
        self._records = records

    def get_closed_pnl(self, **_kw):
        return {"result": {"list": self._records}}


def _ada_record(opened_ms):
    # Shape of a Bybit V5 /v5/position/closed-pnl row for 6549's close.
    return {
        "side": "Buy", "qty": "406", "avgEntryPrice": "0.2391",
        "avgExitPrice": "0.2477", "closedPnl": "-3.60",
        "createdTime": str(opened_ms + 63_000_000),
        "updatedTime": str(opened_ms + 63_000_000),
    }


def _lookup(entry_target):
    opened = 1_760_000_000_000
    return clients._bybit_closed_pnl_lookup(
        _Client([_ada_record(opened)]), category="linear", symbol="ADAUSDT",
        side="Buy", start_ts_ms=opened - 60_000, end_ts_ms=opened + 70_000_000,
        qty_target=406.0, entry_price_target=entry_target, opened_at_ms=opened,
    )


def test_journal_intended_entry_misses_the_venue_record():
    """The defect: 0.2394 vs 0.2391 is 12.5 bps, outside the 10 bps filter."""
    assert _lookup(0.2394) is None


def test_venue_entry_fill_matches_the_venue_record():
    rec = _lookup(0.2391)
    assert rec is not None and float(rec["avgExitPrice"]) == 0.2477


# ── _venue_entry_price ───────────────────────────────────────────────────────

def test_venue_entry_prefers_the_entry_orders_average_fill():
    assert om._venue_entry_price(0.2394, {"avg_price": 0.2391}) == 0.2391


def test_venue_entry_falls_back_to_the_journal_when_unknown():
    for status in (None, {}, {"avg_price": 0.0}, {"avg_price": None}):
        assert om._venue_entry_price(1.3464, status) == 1.3464


# ── Both callers pass the venue entry ────────────────────────────────────────

class _Row(dict):
    """sqlite3.Row-like: subscriptable and has keys()."""


def _trade_6549():
    return _Row({
        "id": 6549, "symbol": "ADAUSDT", "direction": "short",
        "position_size": 406.0, "entry_price": 0.2394,
        "created_at": "2026-10-09T08:04:23.484066+00:00",
        "notes": json.dumps({"trade_id": _ADA_ENTRY_OID, "is_dry": False}),
        "setup_type": "ada_pullback_2h", "account_id": "bybit_2",
        "status": "open",
    })


_CFG = {"account_id": "bybit_2", "exchange": "bybit"}
_ENTRY_STATUS = {"order_id": _ADA_ENTRY_OID, "status": "Filled",
                 "filled_qty": 406.0, "avg_price": 0.2391, "exec_time": None}


def test_watchdog_recovery_matches_on_the_venue_entry_fill():
    seen = {}

    def _fake_lookup(cfg, **kw):
        seen.update(kw)
        return None

    with patch.object(clients, "account_has_broker_pnl_reader", return_value=True), \
         patch.object(clients, "account_order_status", return_value=_ENTRY_STATUS) as aos, \
         patch.object(clients, "account_closed_pnl_for_trade", side_effect=_fake_lookup):
        om._recover_close_from_broker_pnl(None, _trade_6549(), _CFG, "now")
    aos.assert_called_once_with(_CFG, _ADA_ENTRY_OID)
    assert seen["entry_price"] == 0.2391


def test_watchdog_recovery_never_asks_for_a_synthetic_order_id():
    row = _trade_6549()
    row["notes"] = json.dumps({"trade_id": "dry-abc123"})
    seen = {}
    with patch.object(clients, "account_has_broker_pnl_reader", return_value=True), \
         patch.object(clients, "account_order_status") as aos, \
         patch.object(clients, "account_closed_pnl_for_trade",
                      side_effect=lambda cfg, **kw: seen.update(kw)):
        om._recover_close_from_broker_pnl(None, row, _CFG, "now")
    aos.assert_not_called()
    assert seen["entry_price"] == 0.2394


def test_watchdog_recovery_survives_an_order_status_failure():
    seen = {}
    with patch.object(clients, "account_has_broker_pnl_reader", return_value=True), \
         patch.object(clients, "account_order_status", side_effect=RuntimeError("net")), \
         patch.object(clients, "account_closed_pnl_for_trade",
                      side_effect=lambda cfg, **kw: seen.update(kw)):
        om._recover_close_from_broker_pnl(None, _trade_6549(), _CFG, "now")
    assert seen["entry_price"] == 0.2394


def test_reconciler_close_matches_on_the_entry_orders_fill(tmp_path):
    """`_close_trade_from_order_status` already holds the entry order's status."""
    seen = {}

    class _DB:
        def update_trade(self, *_a, **_k):
            pass

        def connect(self):
            return sqlite3.connect(tmp_path / "j.db")

    with patch.object(clients, "account_closed_pnl_for_trade",
                      side_effect=lambda cfg, **kw: seen.update(kw)), \
         patch.object(om, "_cancel_closed_row_protection"), \
         patch.object(om, "_cascade_close_linked_package"), \
         patch.object(om, "_cascade_close_netted_siblings"), \
         patch.object(clients, "account_exec_type_for_close", return_value=None):
        om._close_trade_from_order_status(
            _DB(), dict(_trade_6549()), _ENTRY_STATUS, cfg=_CFG)
    assert seen["entry_price"] == 0.2391


# ── The label heals from venue order identity ────────────────────────────────

def _watchdog_row(tid, sym, qty, sl_oid, tp_oid, setup):
    return {"id": tid, "account_id": "bybit_2", "symbol": sym, "direction": "short",
            "position_size": qty, "exit_reason": "stuck_strategy_watchdog",
            "sl_order_id": sl_oid, "tp_order_id": tp_oid, "setup_type": setup,
            "notes": json.dumps({"closed_by": "stuck_strategy_watchdog",
                                 "exit_price_source": "candle_at_close"})}


def _ada_6549_watchdog_case():
    row = _watchdog_row(6549, "ADAUSDT", 406.0, _ADA_SL_OID,
                        "466d4c31-25c3-43c1-874a-2b96c53f7827", "ada_pullback_2h")
    fills = [_fill("e727e312-99b0-5c90-8478-e5462df2afd9", "bybit_2",
                   "ADA/USDT:USDT", "buy", 0.2477, 406.0,
                   _ago(hours=2, minutes=2, seconds=25), _ADA_SL_OID)]
    return row, fills


def test_watchdog_closes_relabel_sl_from_their_own_stop_order(tmp_path):
    ada, ada_fills = _ada_6549_watchdog_case()
    xrp = _watchdog_row(6528, "XRPUSDT", 56.9, _XRP_SL_OID,
                        "045b9797-1643-4ad5-9edb-a37b80d96e4a",
                        "trend_donchian_xrp_4h")
    xrp_fills = [
        # The two netted xrp_pullback_2h buys that shrank the short 61.6 -> 56.9,
        # then the stop. The LAST exit fill is the trade's own sl order.
        _fill("bc7dd704", "bybit_2", "XRP/USDT:USDT", "buy", 1.3893, 3.3,
              _ago(hours=6), "bbcadf1f-5c39-44af-97c4-affc89b4ccf6"),
        _fill("f957252b", "bybit_2", "XRP/USDT:USDT", "buy", 1.3933, 1.4,
              _ago(hours=4), "1672b5d4-b4ec-472e-a379-febbc61d9c10"),
        _fill("c09d5afe", "bybit_2", "XRP/USDT:USDT", "buy", 1.4024, 56.9,
              _ago(hours=2, minutes=2, seconds=22), _XRP_SL_OID),
    ]
    s, out = _run_bracket_sweep(tmp_path, [ada, xrp], fills=ada_fills + xrp_fills)
    for tid in (6549, 6528):
        assert out[tid]["exit_reason"] == "sl", tid
        n = json.loads(out[tid]["notes"])
        assert n["pre_label_exit_reason"] == "stuck_strategy_watchdog"
        assert n["exit_reason_source"] == "venue_bracket_order"
    assert s["relabelled"] == 2


def test_watchdog_label_stays_when_no_bracket_order_ended_it(tmp_path):
    ada, _ = _ada_6549_watchdog_case()
    fills = [_fill("x", "bybit_2", "ADA/USDT:USDT", "buy", 0.2477, 406.0,
                   _ago(hours=2, minutes=2), "operator-flatten-order")]
    s, out = _run_bracket_sweep(tmp_path, [ada], fills=fills)
    assert out[6549]["exit_reason"] == "stuck_strategy_watchdog"
    assert s["no_bracket_fill"] == 1
