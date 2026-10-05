"""CA-A01-005 (PI-20260927-KFWRL9R1-0002): the close path's "position already
closed on the venue" recogniser must match the rejection Bybit actually sends.

pybit raises ``InvalidRequestError: current position is zero, cannot fix
reduce-only order qty (ErrCode: 110017) ...`` and the non-raising branch of
``close_open_position`` formats only ``retMsg``. The old matcher looked for
``retCode=110017`` / ``position size is zero`` and matched neither, so an
SL/TP-already-fired close was left open in the DB and paged as a failure.
"""
from __future__ import annotations

import pytest

from src.runtime import order_monitor as om
from tests.test_close_retry_cooldown import _FakeDB

_REAL_RAISED = (
    "InvalidRequestError: current position is zero, cannot fix reduce-only "
    "order qty (ErrCode: 110017) (ErrTime: 07:12:03).\nRequest → POST "
    "https://api.bybit.com/v5/order/create: {...}"
)


@pytest.mark.parametrize("err, resp, expected", [
    (_REAL_RAISED, None, True),
    # Non-raising branch: retMsg only, code in the structured envelope.
    ("current position is zero, cannot fix reduce-only order qty",
     {"retCode": 110017, "retMsg": "current position is zero"}, True),
    ("some message", {"retCode": "110017"}, True),
    ("retCode=30031", None, True),
    ("retCode=110017", None, True),
    ("position size is zero", None, True),
    # 110025 is "position mode not modified" — NOT a flatness signal.
    ("retCode=110025", None, False),
    ("Position mode is not modified (ErrCode: 110025)", {"retCode": 110025}, False),
    # Codes that merely CONTAIN an already-flat code must not match.
    ("ErrCode: 1100171", None, False),
    ("venue error retCode=10001 SL race", {"retCode": 10001}, False),
    ("", None, False),
])
def test_already_flat_recogniser(err, resp, expected):
    assert om._venue_reports_position_already_flat(err, resp) is expected


_MATCHED = {
    "id": 7001, "account_id": "bybit_1", "symbol": "XRPUSDT",
    "direction": "long", "position_size": 100,
    "status": "open", "order_package_id": "pkg-xrp", "is_backtest": 0,
}
_OPEN_PKG = {
    "order_package_id": "pkg-xrp", "linked_trade_id": 7001,
    "strategy_name": "xrp_pullback_1h", "symbol": "XRPUSDT",
}


def test_real_110017_rejection_closes_the_db_leg_without_a_fail_streak(monkeypatch):
    om._CLOSE_FAIL_STREAK.clear()
    monkeypatch.setattr(
        om, "_send_close_to_exchange",
        lambda _t: {"ok": False, "error": _REAL_RAISED,
                    "exchange_response": None, "exchange_order_id": None},
    )
    db = _FakeDB(_MATCHED)
    s = om._StrategyTickSummary()
    om._apply_update(db, _OPEN_PKG, {"action": "close", "reason": "sl_cross"}, s)

    assert s.error_count == 0
    assert any(str(tid) == "7001" and u.get("status") == "closed"
               for tid, u in db.trade_updates), db.trade_updates
    assert ("bybit_1", "XRPUSDT", "long") not in om._CLOSE_FAIL_STREAK
