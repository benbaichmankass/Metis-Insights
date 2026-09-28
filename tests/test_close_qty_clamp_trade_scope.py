"""BL-20260907-IB-CLOSE-QTY-CLAMP-IS-SYMBOL-SCOPED-LIKE-THE-CONFIRMATION-WAS.

``IBClient._locked_close``'s Step 0 clamp used to derive ``close_qty`` from
``min(requested_qty, live_qty)`` alone — ``live_qty`` being the whole
SYMBOL's aggregate live position, read with no knowledge of whether other
journal trades share that symbol. The same scope confusion MI-168 fixed one
gate downstream (the close CONFIRMATION), one step earlier in the same
method.

NEVER MEASURED IN PRODUCTION — filed from a code read, not an incident — and
it fails in the SAFE direction (the clamp can only ever UNDER-close relative
to the venue, never flip the position). The residual concern: if journal
drift ever left the symbol's live aggregate short of
``requested_qty + sibling_qty`` (e.g. this trade's own venue position already
partially reduced elsewhere without the journal catching up), the pre-fix
clamp would size the close to whatever the SYMBOL's aggregate had left —
which could include lots that belong, in journal terms, to a SIBLING trade
still legitimately open on the same symbol.

The fix (two layers, mirroring the MI-168 split between the client and its
caller):

  * ``IBClient.close`` / ``_locked_close`` take a new ``sibling_qty`` kwarg
    (default ``0.0``, byte-for-byte the pre-fix clamp) and reserve it out of
    ``live_qty`` before clamping ``requested_qty`` against what remains.
  * ``execute._ib_sibling_open_qty`` — the only layer that can see the
    journal — sums OTHER open trades' recorded ``position_size`` on the same
    ``(account_id, symbol)``, excluding this trade's own ``trade_id``, and
    ``close_open_position`` forwards it to IBClient for the IB branch only.

``test_ib_close_clamp_reserves_sibling_qty_when_live_is_short`` and
``test_close_open_position_ib_reserves_sibling_qty_from_the_journal``
**FAIL on the pre-fix code** — that is the point of them: a live symbol
position, thinned by drift, that is smaller than this trade's own requested
qty plus a sibling's recorded qty. Both planted trades share one symbol.
"""
from __future__ import annotations

import pytest

from src.units.accounts.execute import close_open_position, _ib_sibling_open_qty
from src.units.db.database import Database
from tests.test_p3_close_wiring import (  # noqa: F401  (fixtures/helpers)
    FakeIB,
    _FakePortfolioItem,
    _ib_client_with,
    fake_ib_module,
)


# ---------------------------------------------------------------------------
# 1. IBClient level — sibling_qty is honoured by the Step 0 clamp
# ---------------------------------------------------------------------------


def test_ib_close_clamp_reserves_sibling_qty_when_live_is_short():
    """Two journal trades share MGC: this trade believes it owns 5, a
    sibling believes it owns 3 (total 8), but the LIVE symbol position has
    drifted down to 6 (short of 8 by 2). Before the fix, close_qty =
    min(5, 6) = 5, i.e. this close would leave only 1 lot for the sibling's
    recorded 3 — eating 2 lots that (in journal terms) are the sibling's.

    After the fix, close_qty = min(5, max(0, 6 - 3)) = 3 — the close never
    reserves less than the sibling's 3, whatever the live aggregate is.
    """
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 6, account="DUQ1")],
        open_trades=[],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 5, sibling_qty=3)

    assert res["retCode"] == 0, res
    _contract, order = fake_ib.placed[0]
    assert float(order.totalQuantity) == 3.0, (
        f"expected close sized to protect the sibling's 3 lots out of a "
        f"live 6, got {order.totalQuantity}"
    )


def test_ib_close_clamp_sibling_qty_zero_matches_pre_fix_single_trade_clamp():
    """No sibling (the overwhelming common case) — byte-for-byte the old
    ``min(requested_qty, live_qty)`` clamp. Regression cover alongside the
    pre-existing ``test_ib_close_clamps_to_live_qty``."""
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 2, account="DUQ1")],
        open_trades=[],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 9)  # DB thinks 9, IB holds 2, no sibling

    assert res["retCode"] == 0
    _contract, order = fake_ib.placed[0]
    assert float(order.totalQuantity) == 2.0


def test_ib_close_clamp_refuses_rather_than_close_into_sibling_when_nothing_left():
    """The live aggregate (4) is entirely accounted for by the sibling's
    recorded 4 — nothing may be safely closed for THIS trade. The close must
    refuse, not silently transmit an order against the sibling's lots."""
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 4, account="DUQ1")],
        open_trades=[],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 5, sibling_qty=4)

    assert res["retCode"] != 0, res
    assert "sibling" in res["retMsg"].lower(), res
    assert fake_ib.placed == []


# ---------------------------------------------------------------------------
# 2. execute.close_open_position level — the journal read is wired for IB
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return Database(db_path=str(db_path))


def _seed_open_trade(db, *, trade_id_hint, symbol, position_size,
                      account_id="ib_live_1"):
    """Insert an OPEN journal trade and return its assigned row id."""
    db.insert_trade({
        "timestamp": "2026-09-07T20:00:00+00:00",
        "symbol": symbol, "direction": "long",
        "entry_price": 100.0, "stop_loss": 98.0, "take_profit_1": 104.0,
        "position_size": position_size, "status": "open", "is_backtest": 0,
        "strategy_name": f"strat-{trade_id_hint}", "account_id": account_id,
        "setup_type": f"strat-{trade_id_hint}",
    })
    rows = db.get_trades(filters={
        "account_id": account_id, "symbol": symbol, "status": "open",
    })
    # newest row (ORDER BY timestamp DESC in get_trades) not yet closed and
    # matching this position_size / strategy tag identifies the one just
    # inserted, robust to insertion order across the two seed calls below.
    for row in rows:
        if (row.get("strategy_name") == f"strat-{trade_id_hint}"
                and float(row.get("position_size") or 0.0) == position_size):
            return row["id"]
    raise AssertionError("seeded trade not found back in the journal")


def test_ib_sibling_open_qty_sums_other_open_trades_excluding_self(tmp_journal):
    """Direct unit cover of the helper the fix adds."""
    trade_a = _seed_open_trade(
        tmp_journal, trade_id_hint="A", symbol="MGC", position_size=5.0)
    trade_b = _seed_open_trade(
        tmp_journal, trade_id_hint="B", symbol="MGC", position_size=3.0)

    # Trade A's siblings: just B's 3 lots.
    assert _ib_sibling_open_qty("ib_live_1", "MGC", trade_a) == 3.0
    # Trade B's siblings: just A's 5 lots.
    assert _ib_sibling_open_qty("ib_live_1", "MGC", trade_b) == 5.0
    # A DIFFERENT symbol has no siblings at all.
    assert _ib_sibling_open_qty("ib_live_1", "MHG", trade_a) == 0.0
    # No trade_id excluded -> both rows counted.
    assert _ib_sibling_open_qty("ib_live_1", "MGC", None) == 8.0


def test_close_open_position_ib_reserves_sibling_qty_from_the_journal(
    tmp_journal,
):
    """End-to-end: ``close_open_position`` looks the sibling up itself and
    forwards it to ``IBClient.close`` — the caller never has to compute it.

    Same drift scenario as the client-level test above (live 6, requested 5,
    sibling recorded 3), but reached through the journal + the real
    dispatch path a live close actually takes.
    """
    trade_a = _seed_open_trade(
        tmp_journal, trade_id_hint="A", symbol="MGC", position_size=5.0)
    _seed_open_trade(
        tmp_journal, trade_id_hint="B", symbol="MGC", position_size=3.0)

    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 6, account="DUQ1")],
        open_trades=[],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")
    account_cfg = {"exchange": "interactive_brokers", "account_id": "ib_live_1"}

    outcome = close_open_position(
        client, account_cfg,
        symbol="MGC", side="long", qty=5.0,
        trade_id=trade_a,
    )

    assert outcome["ok"] is True, outcome
    _contract, order = fake_ib.placed[0]
    assert float(order.totalQuantity) == 3.0, (
        f"close_open_position must reserve sibling B's 3 lots before "
        f"forwarding to IBClient; got {order.totalQuantity}"
    )


def test_close_open_position_ib_no_trade_id_matches_pre_fix_behaviour(
    tmp_journal,
):
    """A caller that doesn't pass ``trade_id`` (every call site except
    ``order_monitor._send_close_to_exchange`` today) gets sibling_qty=0,
    i.e. byte-for-byte the old clamp — even with a sibling present in the
    journal, since the caller opted out of trade-scoping."""
    _seed_open_trade(
        tmp_journal, trade_id_hint="A", symbol="MGC", position_size=5.0)
    _seed_open_trade(
        tmp_journal, trade_id_hint="B", symbol="MGC", position_size=3.0)

    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 6, account="DUQ1")],
        open_trades=[],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")
    account_cfg = {"exchange": "interactive_brokers", "account_id": "ib_live_1"}

    outcome = close_open_position(
        client, account_cfg, symbol="MGC", side="long", qty=5.0,
    )

    assert outcome["ok"] is True, outcome
    _contract, order = fake_ib.placed[0]
    assert float(order.totalQuantity) == 5.0, (
        "no trade_id supplied -> sibling_qty=0 -> old min(requested, live) "
        f"clamp; got {order.totalQuantity}"
    )
