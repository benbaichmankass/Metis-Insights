"""ORDER-AUDIT-3 — a monitor-verdict close carries an ``exit_reason_source``.

PI-20260929-TWEW1FZJ-0002. MEASURED 2026-10-15 (diag journal, trades
limit=1000; real-money closes on bybit_2 / bybit_portfolio / alpaca_live with
closed_at >= 2026-10-08, n=16): 6 ``sl`` rows carried
``exit_reason_source=venue_bracket_order``, 2 ``tp`` rows
``price_vs_pkg_bracket`` — and all 4 ``sl_cross`` rows (6565, 6566, 6593,
6594) carried NONE. ``_apply_update``'s full-close branch wrote ``exit_reason``
from the verdict and only ever stamped ``exit_price_source``.

All four had in fact been closed by the venue's own stop order first (venue
fills store via /api/bot/pnl/exchange/fills): 6565 bybit_2 ADAUSDT sold 548 @
0.2432 at 14:38:33.408 on its ``sl_order_id`` 71c1cc1e…, 53 s before the
monitor wrote the close; 6593 bybit_2 ETHUSDT bought 0.05 @ 2506.89 at
03:32:35.388 on ``sl_order_id`` 547adb49…, 45 s before. So they reached the
close branch through ``already_closed_on_exchange`` and the verdict's
``sl_cross`` was not why the position ended.
"""
from __future__ import annotations

import json

from src.runtime import order_monitor as om
from src.runtime import provenance as P

from tests.test_decided_close_provenance_stamp import _FakeDB, _stamped_notes
from tests.test_exit_cluster_flat_reconciled_relabel import (
    _ago,
    _fill,
    _run_bracket_sweep,
)

_SL_OID_6565 = "71c1cc1e-2289-4f63-90fa-d9f4fea2d598"

_MATCHED = {
    "id": 6565, "account_id": "bybit_2", "symbol": "ADAUSDT",
    "direction": "long", "position_size": 548.0,
    "status": "open", "order_package_id": "pkg-b579b3ec33e34571",
    "is_backtest": 0,
}
_OPEN_PKG = {
    "order_package_id": "pkg-b579b3ec33e34571", "linked_trade_id": 6565,
    "strategy_name": "ada_pullback_2h", "symbol": "ADAUSDT",
}
_VERDICT = {"action": "close", "reason": "sl_cross", "exit_price": 0.2431}

# pybit's real rejection text for a reduce-only close on a flat position.
_ALREADY_FLAT = {
    "ok": False,
    "error": ("InvalidRequestError: current position is zero, cannot fix "
              "reduce-only order qty (ErrCode: 110017)"),
    "exchange_response": None, "exchange_order_id": None,
}


def _drive(monkeypatch, send_result, fill_details=None, matched=None):
    monkeypatch.setattr(om, "_send_close_to_exchange", lambda _t: dict(send_result))
    monkeypatch.setattr(om, "_capture_fill_details",
                        lambda *_a, **_k: fill_details)
    db = _FakeDB(matched or _MATCHED)
    om._apply_update(db, dict(_OPEN_PKG), dict(_VERDICT), om._StrategyTickSummary())
    return db


def _close_update(db):
    return next(u for _t, u in db.trade_updates if u.get("status") == "closed")


# ── the regression ──────────────────────────────────────────────────────────

def test_monitor_sent_close_stamps_strategy_verdict(monkeypatch):
    db = _drive(monkeypatch, {"ok": True, "exchange_order_id": "ex-1"},
                {"avg_price": 0.2431, "filled_qty": 548.0})
    notes = _stamped_notes(db)
    assert notes is not None
    assert notes.get("exit_reason_source") == om.VERDICT_LABEL_SOURCE, (
        "an sl_cross close wrote no exit_reason_source — the original bug")
    assert _close_update(db)["exit_reason"] == "sl_cross"
    assert P.classify(notes["exit_reason_source"], "exit_reason_source") == P.ESTIMATED


def test_venue_already_flat_is_stamped_distinctly(monkeypatch):
    """6565's actual path: the venue's stop fired first; the close is refused
    with 110017 and falls through to the DB write. The verdict's reason must not
    read as evidence of why the position ended."""
    db = _drive(monkeypatch, _ALREADY_FLAT)
    notes = _stamped_notes(db)
    assert notes is not None
    assert notes.get("exit_reason_source") == om.VERDICT_VENUE_FLAT_LABEL_SOURCE
    assert _close_update(db)["exit_reason"] == "sl_cross"
    assert P.classify(notes["exit_reason_source"], "exit_reason_source") == P.UNVERIFIED


def test_an_existing_reason_source_is_never_overwritten(monkeypatch):
    pre = dict(_MATCHED)
    pre["notes"] = json.dumps({"exit_reason_source": "venue_bracket_order",
                               "exit_price_source": "exchange_fill"})
    db = _drive(monkeypatch, {"ok": True, "exchange_order_id": "ex-1"},
                {"avg_price": 0.2431}, matched=pre)
    notes = _stamped_notes(db)
    if notes is not None:
        assert notes["exit_reason_source"] == "venue_bracket_order"


def test_constants_match_the_registered_vocabulary():
    assert om.VERDICT_VENUE_FLAT_LABEL_SOURCE == P.EXIT_LABEL_VERDICT_VENUE_FLAT
    assert om.VERDICT_LABEL_SOURCE in P.ESTIMATED_SOURCES
    for bucket in (P.MEASURED_SOURCES, P.ESTIMATED_SOURCES, P.FABRICATED_SOURCES):
        assert om.VERDICT_VENUE_FLAT_LABEL_SOURCE not in bucket


# ── the venue-flat marker is resolved by venue order identity ───────────────

def _row(exit_reason, reason_source):
    notes = {"exit_price_source": "bybit_closed_pnl"}
    if reason_source:
        notes["exit_reason_source"] = reason_source
    return {"id": 6565, "account_id": "bybit_2", "symbol": "ADAUSDT",
            "direction": "long", "position_size": 548.0,
            "exit_reason": exit_reason, "sl_order_id": _SL_OID_6565,
            "tp_order_id": "f68fde6d-0dc3-4f4f-b07c-5732fad29fc4",
            "setup_type": "ada_pullback_2h",
            "notes": om.dump_capped(notes, 2000)}


_FILLS_6565 = [_fill("x6565", "bybit_2", "ADA/USDT:USDT", "sell", 0.2432, 548.0,
                     _ago(hours=2, minutes=1), _SL_OID_6565)]


def test_venue_flat_verdict_close_is_relabelled_from_its_stop_order(tmp_path):
    s, out = _run_bracket_sweep(
        tmp_path, [_row("sl_cross", om.VERDICT_VENUE_FLAT_LABEL_SOURCE)],
        fills=_FILLS_6565)
    assert out[6565]["exit_reason"] == "sl"
    n = json.loads(out[6565]["notes"])
    assert n["exit_reason_source"] == "venue_bracket_order"
    assert n["pre_label_exit_reason"] == "sl_cross"
    assert s["relabelled"] == 1


def test_a_monitor_sent_verdict_close_is_never_relabelled(tmp_path):
    s, out = _run_bracket_sweep(
        tmp_path, [_row("sl_cross", om.VERDICT_LABEL_SOURCE)], fills=_FILLS_6565)
    assert out[6565]["exit_reason"] == "sl_cross" and s["scanned"] == 0


def test_venue_flat_row_marked_concurrently_is_not_overwritten(tmp_path):
    def _operator_marks(c):
        c.execute("UPDATE trades SET exit_reason='operator_flatten_reconciled' "
                  "WHERE id=6565")
    s, out = _run_bracket_sweep(
        tmp_path, [_row("sl_cross", om.VERDICT_VENUE_FLAT_LABEL_SOURCE)],
        fills=_FILLS_6565, mutate_before_write=_operator_marks)
    assert out[6565]["exit_reason"] == "operator_flatten_reconciled"
    assert s["lost_race"] == 1 and s["relabelled"] == 0
