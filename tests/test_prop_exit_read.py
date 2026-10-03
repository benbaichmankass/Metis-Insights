"""PROP-EXIT-READ: the executor's close report carries the exit read from the
DXtrade Trade History (exit price, net P&L, tp / sl / manual), and keeps the
"not built" reason whenever the row cannot be matched unambiguously.

The table shape is the MEASURED one: run 36611267485 (issue #14348), headers
and rows verbatim (ids masked there, as here)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.prop import prop_executor as pe
from src.prop.platform.base import AccountSnapshot
from src.prop.platform.dxtrade import (
    DXtradeAdapter,
    _is_orders_table,
    orders_from_tables,
    parse_history_time,
    trade_history_from_tables,
)

HEADERS = ["Date and Time", "Symbol", "Order ID", "Trade Code", "Side", "Position effect", "Trade Volume",
           "Trade Price", "Commission", "Closed P&L", "Net Closed P&L", ""]
ROWS = [
    ["29/09/26 18:18", "SOLUSD", "#####", "#####:#####", "Sell", "Closing", "0.01", "118.45", "−0.01", "−0.01", "−0.03", ""],
    ["29/09/26 18:18", "SOLUSD", "#####", "#####:#####", "Buy", "Opening", "0.01", "118.52", "−0.01", "—", "—", ""],
    ["29/09/26 15:06", "SOLUSD", "#####", "#####:#####", "Sell", "Closing", "0.01", "119.36", "−0.01", "−0.02", "−0.04", ""],
    ["29/09/26 06:27", "SOLUSD", "#####", "#####:#####", "Buy", "Opening", "0.01", "118.94", "−0.01", "—", "—", ""],
    ["25/09/26 09:28", "ETHUSD", "#####", "#####:#####", "Buy", "Closing", "1.30", "2,711.10", "−1.41", "−58.49", "−63.63", ""],
    ["23/09/26 14:13", "ETHUSD", "#####", "#####:#####", "Sell", "Opening", "1.30", "2,666.11", "−1.39", "—", "—", ""],
]
TABLE = {"kind": "table", "headers": HEADERS, "rows": ROWS}
WATCHLIST = {"kind": "table", "headers": ["Symbol", "Bid", "Ask", "Change", "Chg%", "Description", ""],
             "rows": [["SOLUSD", "118.50", "118.51", "−0.33", "−0.28", "SOL", ""]]}
UTC = timezone.utc


# ── the parser ──────────────────────────────────────────────────────────────


def test_trade_history_table_is_not_a_working_orders_table():
    # Fails on the code before PROP-EXIT-READ: #14348 printed reads_as=orders
    # for this table, so a history row could be read as a working order.
    assert not _is_orders_table(HEADERS)
    assert orders_from_tables([WATCHLIST, TABLE]) is None


def test_parses_the_measured_rows():
    got = trade_history_from_tables([WATCHLIST, TABLE])
    assert len(got) == 6
    eth_close = got[4]
    assert eth_close == {
        "time": "25/09/26 09:28", "ts": datetime(2026, 9, 25, 9, 28, tzinfo=UTC), "symbol": "ETHUSD",
        "side": "long", "effect": "closing", "volume": 1.30, "price": 2711.10, "commission": -1.41,
        "closed_pnl": -58.49, "net_closed_pnl": -63.63,
    }
    assert got[1]["effect"] == "opening" and got[1]["closed_pnl"] is None and got[1]["net_closed_pnl"] is None
    # No account identifiers are carried.
    assert all("order_id" not in r and "trade_code" not in r for r in got)


def test_no_history_table_is_none_and_an_empty_one_is_empty():
    assert trade_history_from_tables([WATCHLIST]) is None
    assert trade_history_from_tables([{"headers": HEADERS, "rows": []}]) == []


@pytest.mark.parametrize("text,want", [
    ("29/09/26 18:18", datetime(2026, 9, 29, 18, 18, tzinfo=UTC)),
    ("02/10/2026 18:50:07", datetime(2026, 10, 2, 18, 50, 7, tzinfo=UTC)),
    ("", None), ("—", None), ("2026-10-02", None),
])
def test_parse_history_time(text, want):
    assert parse_history_time(text) == want


# ── the adapter read: view tabs only ───────────────────────────────────────


def test_read_trade_history_clicks_only_view_tabs_and_restores_orders():
    ad = DXtradeAdapter()
    clicked = []
    ad._show_tab = lambda page, key: clicked.append(key) or True
    ad._tables = lambda page: [WATCHLIST, TABLE]
    got = ad.read_trade_history(page=None)
    assert clicked == ["tab_trade_history", "tab_orders"]
    assert len(got) == 6


def test_read_trade_history_raises_and_still_restores_when_the_tab_is_missing():
    ad = DXtradeAdapter()
    clicked = []
    ad._show_tab = lambda page, key: clicked.append(key) or key != "tab_trade_history"
    ad._tables = lambda page: pytest.fail("must not read a table when the tab did not open")
    with pytest.raises(LookupError):
        ad.read_trade_history(page=None)
    assert clicked == ["tab_trade_history", "tab_orders"]


# ── matching and the reason ────────────────────────────────────────────────


@pytest.mark.parametrize("direction,exit_px,want", [
    ("short", 2718.46, "sl"), ("short", 2719.90, "sl"),      # at / past the stop
    ("short", 2394.06, "tp"), ("short", 2393.00, "tp"),      # at / past the target
    ("short", 2550.00, "manual"),
    ("long", 119.36, "sl"), ("long", 126.0, "tp"), ("long", 121.0, "manual"),
    ("long", None, "manual"),
])
def test_exit_reason(direction, exit_px, want):
    sl, tp = (2718.46, 2394.06) if direction == "short" else (119.36, 126.0)
    assert pe.exit_reason(direction, exit_px, sl, tp) == want


def _hist(*rows):
    return trade_history_from_tables([{"headers": HEADERS, "rows": list(rows)}])


# MEASURED (run 37051715764, issue #15629): the executor's first automated
# position as Trade History shows it. The close row is constructed.
ETH_OPEN = ["02/10/26 18:51", "ETHUSD", "#####", "#####:#####", "Sell", "Opening", "1.22", "2,657.17", "−1.30", "—", "—", ""]
ETH_TP = ["03/10/26 02:11", "ETHUSD", "#####", "#####:#####", "Buy", "Closing", "1.22", "2,394.06", "−1.30", "321.00", "318.40", ""]
J = {"symbol": "ETHUSDT", "direction": "short", "qty": 1.22, "sl": 2718.46, "tp": 2394.06,
     "created_at": "2026-10-02T18:51:30+00:00", "ticket_id": "prop-manual-x"}
LATER = datetime(2026, 10, 3, 3, 0, tzinfo=UTC)


def test_match_exit_finds_the_one_closing_trade():
    hit, why = pe.match_exit(J, _hist(ETH_TP, ETH_OPEN, *ROWS), LATER)
    assert why == "matched" and hit["price"] == 2394.06 and hit["net_closed_pnl"] == 318.4


@pytest.mark.parametrize("rows,why", [
    ([ETH_OPEN], "0 closing trades match"),                                     # not closed in history yet
    ([ROWS[4]], "0 closing trades match"),                                      # an older ETH close only
    ([ETH_TP[:6] + ["0.61"] + ETH_TP[7:], ETH_OPEN], "0 closing trades match"),  # partial close
    ([ETH_TP, ETH_TP[:7] + ["2,500.00"] + ETH_TP[8:]], "2 closing trades match"),  # two candidates
    ([ETH_TP[:4] + ["Sell"] + ETH_TP[5:]], "0 closing trades match"),           # same side: not a close of a short
])
def test_match_exit_refuses_anything_ambiguous(rows, why):
    hit, got = pe.match_exit(J, _hist(*rows), LATER)
    assert hit is None and got == why


def test_match_exit_needs_a_timestamp_and_qty():
    assert pe.match_exit({**J, "created_at": None}, _hist(ETH_TP), LATER)[0] is None
    assert pe.match_exit({**J, "qty": None}, _hist(ETH_TP), LATER)[0] is None


def test_match_exit_ignores_a_row_after_now():
    assert pe.match_exit(J, _hist(ETH_TP), datetime(2026, 10, 3, 2, 0, tzinfo=UTC))[0] is None


# ── the cycle: what the close report says, and that WHEN is unchanged ─────

NOW = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
SOL_J = {"id": 1, "account_id": "breakout_1", "symbol": "SOLUSDT", "direction": "long", "status": "open",
         "ticket_id": "tx", "qty": 0.5, "entry_price": 120.0, "sl": 118.0, "tp": 126.0,
         "created_at": "2026-09-30T08:00:00+00:00"}
SOL_TP = ["30/09/26 08:40", "SOLUSD", "#####", "#####:#####", "Sell", "Closing", "0.50", "126.01", "−0.01", "3.00", "2.98", ""]


class Ad:
    def __init__(self, history=None, error=None):
        self.history, self.error, self.reads = history, error, 0

    def read_account(self, page):
        return AccountSnapshot(balance=5000.0, equity=5000.0, unrealized=0.0, realized_today=0.0)

    def read_positions(self, page):
        return []

    def read_orders(self, page):
        return []

    def read_trade_history(self, page):
        self.reads += 1
        if self.error:
            raise LookupError(self.error)
        return self.history


class NoHistoryAd(Ad):
    read_trade_history = None


class Api:
    def __init__(self, fills):
        self._fills, self.posts = list(fills), []

    def tickets(self, account_id):
        return []

    def open_fills(self, account_id):
        return pe.open_from_fills(self._fills)

    def post_report(self, body):
        self.posts.append(body)
        return {"ok": True}


def _cycles(tmp_path, ad, api, n=2):
    ledger = pe.IntentLedger(tmp_path / "ledger.jsonl")
    state = pe.ExecutorState(tmp_path)
    state.save({"day": pe.trading_day(NOW), "day_start_captured": 5000.0})
    cfg = pe.ExecutorConfig(account_size_usd=5000.0, daily_loss_pct=0.03, max_dd_pct=0.06, safety_margin_usd=5.0,
                            risk_cap_usd=75.0, unconfirmed_reads=3,
                            symbols={"SOLUSDT": {"venue": "SOLUSD", "cvpp": 1.0, "lot_units": 1.0,
                                                 "min_lots": 0.01, "lot_step": 0.01}})
    for _ in range(n):
        pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg, mode="live", ledger=ledger, state=state, now=NOW)
    return [p for p in api.posts if p.get("status") == "closed"]


def test_close_report_carries_the_exit_read_from_trade_history(tmp_path):
    # Fails on the code before PROP-EXIT-READ: the close carried no exit_price,
    # no pnl and the "not built" reason.
    ad = Ad(history=_hist(SOL_TP, *ROWS))
    closed = _cycles(tmp_path, ad, Api([SOL_J]))
    assert len(closed) == 1
    c = closed[0]
    assert c["exit_price"] == 126.01 and c["pnl"] == 2.98 and c["reason"] == "tp"
    assert c["closed_at"] == "2026-09-30T08:40:00+00:00" and c["ticket_id"] == "tx"
    assert ad.reads == 1          # read only on the cycle that reports the close


def test_close_timing_is_unchanged_and_history_is_not_read_before_it(tmp_path):
    ad = Ad(history=_hist(SOL_TP))
    assert _cycles(tmp_path, ad, Api([SOL_J]), n=1) == [] and ad.reads == 0


@pytest.mark.parametrize("ad", [
    Ad(history=_hist(*ROWS)),                          # no matching close row
    Ad(error="Trade History tab not found"),           # the read failed
    NoHistoryAd(),                                     # an adapter without the reader
    Ad(history=_hist(SOL_TP, SOL_TP[:7] + ["125.00"] + SOL_TP[8:])),  # ambiguous
])
def test_unmatched_close_keeps_the_gap_visible_and_invents_nothing(tmp_path, ad):
    closed = _cycles(tmp_path, ad, Api([SOL_J]))
    assert len(closed) == 1                            # still closed, on the same cycle
    c = closed[0]
    assert c["reason"] == pe.CLOSE_UNREAD_REASON
    assert "exit_price" not in c and "pnl" not in c
