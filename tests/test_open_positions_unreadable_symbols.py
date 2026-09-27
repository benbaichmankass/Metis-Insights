"""FIX-CA-08 (CA-A03) — a symbol the Bybit read could not look at is not flat.

``account_open_positions``' bybit branch cross-checks every configured symbol
the ``settleCoin=USDT`` page omitted (BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND).
When that symbol-scoped read RAISED, the symbol was simply absent from the
returned list — byte-identical to "the venue says it is flat". Two consumers
then acted on the absence:

  * ``closed_flat_invariant`` graded the symbol ``flat`` (alert-only), and
  * ``_reconcile_open_trades`` armed and then fired its close-confirm on an
    open row whose position was merely unreadable — closing the journal row
    and leaving the live position unprotected and invisible (money at risk).

After the fix the returned list carries ``unreadable_symbols``; the invariant
grades those ``could_not_look`` and the reconciler skips those rows.
"""
from __future__ import annotations

from unittest.mock import patch

from src.runtime.closed_flat_invariant import (
    RESIDUAL_STATE_COULD_NOT_LOOK,
    RESIDUAL_STATE_FLAT,
    _residual_from_positions,
)
from src.units.accounts.clients import account_open_positions
from tests import test_monitor_reconciler as _reconciler
from tests.test_monitor_reconciler import (
    _filled_status,
    _insert_trade,
    _read_trade,
    _reconcile_to_close,
)

# Fixture reused from the reconciler suite (bound, not imported, so the test
# parameter that requests it is not an import redefinition).
tmp_db = _reconciler.tmp_db

_ACCOUNT = {
    "account_id": "bybit_2", "exchange": "bybit",
    "market_type": "linear", "symbols": ["BTCUSDT", "ETHUSDT"],
}


class _PageOmitsBtcAndCrossCheckRaises:
    """settleCoin page lists only ETHUSDT; the BTCUSDT symbol-scoped read raises."""

    def get_positions(self, **kw):
        if "symbol" in kw:
            raise RuntimeError("per-symbol 5xx")
        return {"result": {"list": [
            {"symbol": "ETHUSDT", "side": "Buy", "size": "0.12",
             "avgPrice": "1725.0", "unrealisedPnl": "5.0", "positionIdx": 0},
        ]}}


def _read(client):
    with patch("src.units.accounts.clients.bybit_client_for", return_value=client):
        return account_open_positions(_ACCOUNT)


class TestAccountOpenPositionsCarriesUnreadableSymbols:
    def test_failed_cross_check_symbol_is_unreadable(self):
        out = _read(_PageOmitsBtcAndCrossCheckRaises())
        assert out is not None
        assert [r["symbol"] for r in out] == ["ETHUSDT"]
        assert set(out.unreadable_symbols) == {"BTCUSDT"}

    def test_clean_read_has_no_unreadable_symbols(self):
        class _Clean:
            def get_positions(self, **kw):
                return {"result": {"list": []}}

        out = _read(_Clean())
        assert out == []
        assert set(out.unreadable_symbols) == set()

    def test_non_dict_settlecoin_response_is_a_failed_read(self):
        class _Garbage:
            def get_positions(self, **kw):
                return "<html>502 Bad Gateway</html>"

        assert _read(_Garbage()) is None


class TestInvariantGradesUnreadableSymbolCouldNotLook:
    def test_unreadable_symbol_grades_could_not_look(self):
        out = _read(_PageOmitsBtcAndCrossCheckRaises())
        assert _residual_from_positions(out, "BTCUSDT", "long").residual_state \
            == RESIDUAL_STATE_COULD_NOT_LOOK

    def test_readable_absent_symbol_still_grades_flat(self):
        out = _read(_PageOmitsBtcAndCrossCheckRaises())
        assert _residual_from_positions(out, "ETHUSDT", "short").residual_state \
            == RESIDUAL_STATE_FLAT


class TestReconcilerDoesNotCloseAnUnreadableSymbol:
    def test_open_row_on_unreadable_symbol_is_not_closed(self, tmp_db):
        trade_id = _insert_trade(
            tmp_db, symbol="BTCUSDT", trade_id="2000000000000000777",
        )
        partial = _read(_PageOmitsBtcAndCrossCheckRaises())

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status("2000000000000000777"),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=partial,
        ):
            summary = _reconcile_to_close(tmp_db)  # two ticks, confirm window 0

        assert summary["closed"] == 0
        assert summary["pending_close"] == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "open"
