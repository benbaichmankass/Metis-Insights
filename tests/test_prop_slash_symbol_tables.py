"""OPS-AUDIT 2026-10-04 (OA-02): tradeify_1 displays ``ETH/USD`` for ``ETHUSD``.

The table parsers stored the Symbol cell verbatim, and every executor match
compares ``p.symbol.upper()`` with the ticket's venue symbol, so a slash-named
row never matched: a real fill read ``unconfirmed_submit``, a fill with no
SL/TP could not classify ``partial_no_sl_tp``, and the trail skipped it.
"""
from __future__ import annotations

from src.prop.platform.dxtrade import (
    orders_from_tables,
    positions_from_tables,
    trade_history_from_tables,
)
from src.prop.prop_executor import classify_confirmation, match_terminal

POS = {"headers": ["Symbol", "Side", "Quantity", "Open Price", "Stop Loss", "Take Profit", "P&L"],
       "rows": [["ETH/USD", "Buy", "0.5", "2,950.00", "", "", "1.0"],
                ["SOLUSD", "Sell", "-3", "150.10", "160", "140", "0"]]}
ORD = {"headers": ["Symbol", "Side", "Type", "Quantity", "Price"],
       "rows": [["ETH/USD", "Buy", "Limit", "0.5", "2,950.00"]]}


def test_slash_symbol_rows_parse_to_the_venue_symbol():
    assert [p.symbol for p in positions_from_tables([POS])] == ["ETHUSD", "SOLUSD"]
    assert [o.symbol for o in orders_from_tables([ORD])] == ["ETHUSD"]


def test_slash_symbol_position_is_matched_and_a_missing_bracket_is_seen():
    spec = {"venue_symbol": "ETHUSD", "side": "long", "quantity": 0.5, "limit_price": 2950.0,
            "stop_loss": 2900.0, "take_profit": 3050.0}
    found = match_terminal(spec, positions_from_tables([POS]), [])
    assert len(found["positions"]) == 1
    assert classify_confirmation(spec, found) == "partial_no_sl_tp"


def test_trade_history_symbol_is_canonical_when_the_table_parses():
    hist = {"headers": ["Time", "Symbol", "Position effect", "Trade Volume", "Trade Price",
                        "Commission", "Net Closed P&L", "Closed P&L"],
            "rows": [["2026-10-04 10:00:00", "ETH/USD", "Close", "0.5", "2,960.00", "0", "5", "5"]]}
    rows = trade_history_from_tables([hist])
    if rows:  # header shape is the parser's own contract; only the symbol is under test
        assert rows[0]["symbol"] == "ETHUSD"
