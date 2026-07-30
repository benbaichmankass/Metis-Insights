"""Tests for src/runtime/local_pnl.py + the local-PnL fallback sweep.

The fallback exists because the Bybit closed-pnl sweep
(``_sweep_pending_pnl_from_bybit``) is Bybit-only, so IBKR (MES/MGC/MHG on
``ib_paper``) and other non-Bybit paper trades never get a realised PnL and
render ``$0.00`` (operator report 2026-06-16). The sweep computes it locally
from entry × exit × qty × direction × contract multiplier.

**Updated 2026-07-30 (Tier-2).** Two things changed and several tests here were
rewritten rather than relaxed:

1. The exit price is no longer ``last_mark_price()`` — the market at SWEEP time.
   For a CONFIRMED CLOSE that is FABRICATED. It is now the close of the bar
   covering ``closed_at`` (ESTIMATED), and when no anchor exists the row is
   DECLARED unmeasured instead of priced from an unrelated number.
2. ``interactive_brokers`` is now a declared broker-PnL reader, so IB rows defer
   to it exactly as Bybit rows do instead of falling straight to local compute.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import local_pnl
from src.runtime.order_monitor import _sweep_local_pnl_for_unpriced
from src.units.db.database import Database


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_realized_pnl_long_crypto():
    # cvu=1 (crypto perp): (exit-entry)*qty
    pnl = local_pnl.compute_realized_pnl(
        entry_price=100.0, exit_price=110.0, qty=2.0,
        direction="long", contract_value_usd=1.0,
    )
    assert pnl == 20.0


def test_realized_pnl_short_is_negated():
    pnl = local_pnl.compute_realized_pnl(
        entry_price=110.0, exit_price=100.0, qty=2.0,
        direction="short", contract_value_usd=1.0,
    )
    assert pnl == 20.0  # short profits when price falls


def test_realized_pnl_buy_sell_aliases():
    assert local_pnl.compute_realized_pnl(
        entry_price=100, exit_price=105, qty=1, direction="buy",
    ) == 5.0
    assert local_pnl.compute_realized_pnl(
        entry_price=100, exit_price=105, qty=1, direction="sell",
    ) == -5.0


@pytest.mark.parametrize("symbol,expected_cvu", [
    ("MGC", 10.0),    # micro gold: 10 troy oz
    ("MHG", 2500.0),  # micro copper: 2,500 lb
    ("MES", 5.0),     # micro e-mini S&P: $5/point
    ("BTCUSDT", 1.0),
])
def test_contract_value_usd_for_known_symbols(symbol, expected_cvu):
    assert local_pnl.contract_value_usd_for(symbol) == expected_cvu


def test_realized_pnl_futures_multiplier_applied():
    # MGC long, 13.4-point move, 4 contracts, $10/point → $536.
    cvu = local_pnl.contract_value_usd_for("MGC")
    pnl = local_pnl.compute_realized_pnl(
        entry_price=4286.6, exit_price=4300.0, qty=4.0,
        direction="long", contract_value_usd=cvu,
    )
    assert pnl == pytest.approx(536.0)


def test_realized_pnl_none_on_bad_inputs():
    assert local_pnl.compute_realized_pnl(
        entry_price=None, exit_price=110, qty=1, direction="long") is None
    assert local_pnl.compute_realized_pnl(
        entry_price=100, exit_price=None, qty=1, direction="long") is None
    assert local_pnl.compute_realized_pnl(
        entry_price=100, exit_price=110, qty=0, direction="long") is None
    assert local_pnl.compute_realized_pnl(
        entry_price=100, exit_price=110, qty=1, direction="???") is None


def test_pnl_percent_multiplier_cancels():
    # 10% gain regardless of multiplier (notional = entry*qty*cvu).
    pct = local_pnl.compute_pnl_percent(
        pnl=536.0, entry_price=4286.6, qty=4.0, contract_value_usd=10.0,
    )
    assert pct == pytest.approx(13.4 / 4286.6 * 100, rel=1e-3)


def test_broker_pnl_reader_capability_is_declarative():
    # The broker-vs-local decision is a declared integration capability,
    # not a hardcoded "is it Bybit" check.
    from src.units.accounts.clients import (
        BROKER_PNL_READER_EXCHANGES,
        account_has_broker_pnl_reader,
        exchange_has_broker_pnl_reader,
    )
    assert "bybit" in BROKER_PNL_READER_EXCHANGES
    assert exchange_has_broker_pnl_reader("bybit") is True
    assert exchange_has_broker_pnl_reader("Bybit") is True
    # interactive_brokers joined the set 2026-07-30 (Tier-2): IBKR serves
    # per-execution truth via reqExecutions (CommissionReport.realizedPNL),
    # read back from the exchange-fills store. Two members rather than one is
    # what actually exercises this test's premise — the decision is a DECLARED
    # capability, not a hardcoded "is it Bybit".
    assert "interactive_brokers" in BROKER_PNL_READER_EXCHANGES
    assert exchange_has_broker_pnl_reader("interactive_brokers") is True
    # Integrations with no wired reader → local fallback (still the default).
    assert exchange_has_broker_pnl_reader("alpaca") is False
    assert exchange_has_broker_pnl_reader("oanda") is False
    assert account_has_broker_pnl_reader({"exchange": "bybit"}) is True
    assert account_has_broker_pnl_reader(
        {"exchange": "interactive_brokers"}) is True
    assert account_has_broker_pnl_reader({"exchange": "alpaca"}) is False
    assert account_has_broker_pnl_reader(None) is False


# ---------------------------------------------------------------------------
# Sweep integration
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return Database(db_path=str(db_path))


def _insert(db, **over):
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "MGC",
        "direction": "long",
        "entry_price": 4286.6,
        "position_size": 4.0,
        "status": "orphaned",
        "is_backtest": 0,
        "account_id": "ib_paper",
        "strategy_name": "mgc_trend_1h",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row.update(over)
    return db.insert_trade(row)


def _pnl_of(db, trade_id):
    rows = db.get_trades(filters={"id": trade_id})
    return rows[0] if rows else None


def test_sweep_defers_fresh_ibkr_row_to_the_broker_reader(db, monkeypatch):
    """Was `test_sweep_computes_pnl_for_ibkr_orphan`, and the rewrite IS the
    2026-07-30 Tier-2 change.

    It used to assert the sweep priced this row at 4300.0 from
    `last_mark_price` — the market at SWEEP time, not the exit. For a CONFIRMED
    CLOSE that is FABRICATED: a true value exists (the fill) and the mark is not
    it. It produced a plausible $536.00 with nothing behind it.

    Now `interactive_brokers` is a declared broker reader, so a FRESH row is
    deferred to it (pull_ib_executions -> the fills store) exactly as a Bybit
    row is, rather than being priced from an unrelated number.
    """
    tid = _insert(db)  # orphaned ib_paper MGC long, pnl NULL, created just now
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"ib_paper": {"exchange": "interactive_brokers"}},
    )
    summary = _sweep_local_pnl_for_unpriced(db)
    assert summary["deferred_broker"] == 1
    assert summary["filled"] == 0
    assert _pnl_of(db, tid)["pnl"] is None  # NULL, not an invented 536.00


def test_sweep_declares_unmeasured_when_aged_ibkr_row_has_no_anchor(db, monkeypatch):
    """The other half: once past the broker grace with no anchor available,
    the row must converge to a DECLARATION, not to a mark.

    IBKR historical-candle coverage is 0%, so this is the real steady state for
    an IB close the executions pull never captured. Declaring keeps the row
    visible (INV-2 accepts the marker, INV-2b counts it) without inventing a
    number — the alternative the old test enshrined."""
    import src.runtime.exit_anchor as EA
    from src.runtime.provenance import UNMEASURED_MARKER

    aged = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    tid = _insert(db, created_at=aged)
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"ib_paper": {"exchange": "interactive_brokers"}},
    )
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (None, "no_anchor"))
    summary = _sweep_local_pnl_for_unpriced(db)
    assert summary["declared_unmeasured"] == 1
    assert summary["filled"] == 0
    row = _pnl_of(db, tid)
    assert row["pnl"] is None
    import json
    assert json.loads(row["notes"])["pnl_source"] == UNMEASURED_MARKER


def test_sweep_defers_recent_broker_reader_rows(db, monkeypatch):
    # A recent Bybit (broker-reader) row is deferred to the Bybit sweep so its
    # fee-accurate PnL is never pre-empted by a local estimate.
    tid = _insert(db, symbol="BTCUSDT", account_id="bybit_2",
                  status="closed", entry_price=100.0, position_size=1.0)
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"bybit_2": {"exchange": "bybit"}},
    )
    monkeypatch.setattr(
        "src.runtime.local_pnl.last_mark_price", lambda *a, **k: 110.0,
    )
    summary = _sweep_local_pnl_for_unpriced(db)
    assert summary["deferred_broker"] == 1
    assert summary["filled"] == 0
    assert _pnl_of(db, tid)["pnl"] is None  # left for the Bybit sweep


def test_sweep_rescues_abandoned_broker_row_past_window(db, monkeypatch):
    # A Bybit row OLDER than the broker recovery window (broker can no longer
    # price it) falls back to local compute instead of a permanent $0.00.
    old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    tid = _insert(db, symbol="BTCUSDT", account_id="bybit_2",
                  status="closed", entry_price=100.0, position_size=1.0,
                  direction="long", created_at=old)
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"bybit_2": {"exchange": "bybit"}},
    )
    # The rescue is now ANCHORED to the recorded close time rather than read
    # from `last_mark_price` at sweep time. Same outcome — the row converges
    # instead of stranding NULL — but the number now has something behind it.
    import src.runtime.exit_anchor as EA
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (110.0, "anchored"))
    summary = _sweep_local_pnl_for_unpriced(db)
    assert summary["filled"] == 1
    row = _pnl_of(db, tid)
    assert row["pnl"] == pytest.approx(10.0)  # (110-100)*1*1
    import json
    # ESTIMATED, never MEASURED — a bar close says where the market was, not
    # where THIS order filled.
    assert json.loads(row["notes"])["exit_price_source"] == "candle_at_close"


def test_sweep_rescues_aged_broker_row_with_no_closed_pnl(db, monkeypatch):
    # BL-20260623-001 / real-money #2783: a Bybit row the broker reader can
    # NEVER fill (no closed-pnl record — a same-instant net-flat) used to sit
    # ``closed``/``pnl NULL`` between the 6h INV-2 grace and the 7-day broker
    # retention window, tripping INV-2. Once aged past the 6h grace it now
    # converges to local_compute instead of stranding NULL for days.
    aged = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    tid = _insert(db, symbol="BTCUSDT", account_id="bybit_2",
                  status="closed", entry_price=100.0, position_size=1.0,
                  direction="long", created_at=aged)
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"bybit_2": {"exchange": "bybit"}},
    )
    import src.runtime.exit_anchor as EA
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (110.0, "anchored"))
    summary = _sweep_local_pnl_for_unpriced(db)
    assert summary["filled"] == 1
    assert summary["deferred_broker"] == 0
    row = _pnl_of(db, tid)
    assert row["pnl"] == pytest.approx(10.0)  # (110-100)*1*1
    import json
    notes = json.loads(row["notes"])
    # `local_compute` describes the ARITHMETIC; `exit_price_source` is what
    # says whether the number is trustworthy — which is why classify_pnl reads
    # both keys and treats `local_compute` alone as information-free.
    assert notes.get("pnl_source") == "local_compute"
    assert notes.get("exit_price_source") == "candle_at_close"


def test_sweep_ignores_rejected_zero_size(db, monkeypatch):
    tid = _insert(db, status="rejected", position_size=0.0)
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"ib_paper": {"exchange": "interactive_brokers"}},
    )
    summary = _sweep_local_pnl_for_unpriced(db)
    assert summary["scanned"] == 0  # filtered by status + position_size>0
    assert _pnl_of(db, tid)["pnl"] is None


def test_sweep_still_pending_when_the_anchor_defers(db, monkeypatch):
    """`deferred` means we did NOT look (budget spent / transient read failure),
    so the row must be RETRIED — not priced, and not declared unmeasured.
    Declaring here would record a gap we never actually searched for."""
    import src.runtime.exit_anchor as EA
    from src.runtime.provenance import UNMEASURED_MARKER

    aged = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    tid = _insert(db, created_at=aged)
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"ib_paper": {"exchange": "interactive_brokers"}},
    )
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (None, "deferred"))
    summary = _sweep_local_pnl_for_unpriced(db)
    assert summary["still_pending"] == 1
    assert summary["filled"] == 0
    assert summary["declared_unmeasured"] == 0
    row = _pnl_of(db, tid)
    assert row["pnl"] is None
    import json
    assert json.loads(row["notes"] or "{}").get("pnl_source") != UNMEASURED_MARKER
