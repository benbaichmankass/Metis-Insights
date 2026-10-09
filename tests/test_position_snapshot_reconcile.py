"""Universal position-snapshot reconciliation (P3b, live-trade-management
contract — docs/audits/live-trade-management-contract-2026-06-16.md).

Pins the contract for the position-snapshot pass inside
``src.runtime.order_monitor._reconcile_orphan_exchange_positions``:
a STRATEGY-ATTRIBUTED DB-open trade on an integration WITHOUT a per-order
status reader (IB, Alpaca — everything except Bybit) whose ``(symbol, side)``
is confirmed absent from a SUCCESSFUL ``account_open_positions`` snapshot is
closed (``status='closed'``, ``exit_reason='exchange_flat_reconciled'``) after a
2-observation confirm. The merged local-PnL sweep fills ``pnl`` later
(mark-to-market), so the close itself leaves ``pnl`` NULL.

SAFETY is paramount — a false close (closing a row whose position is still
open) is the worst outcome. These tests pin the conservatism:

* confirmed-absent-twice → closes,
* absent-once → ARMED, NOT closed,
* present in snapshot → not closed + pending cleared,
* read failure (None snapshot) → NEVER closed,
* Bybit row → skipped here (the forward reconciler owns it),
* integration w/o ``open_positions`` cap → skipped (can't reconcile),
* closed row carries ``pnl`` NULL (the sweep fills it later).

Pairs with ``test_reverse_reconciler.py`` (exchange→DB orphan adoption) and
``test_monitor_reconciler.py`` (Bybit forward order-status reconcile).
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.runtime.order_monitor import _reconcile_orphan_exchange_positions
from src.units.db.database import Database


# IB (interactive_brokers) has caps {close, open_positions} — NO order_status,
# so the snapshot reconciler owns it. Bybit has order_status — forward
# reconciler owns it, snapshot pass skips it. OANDA was wired in S2 and now also
# has {close, open_positions}, so it IS snapshot-reconciled like IB. An exchange
# with NO open_positions cap (here a genuinely-unknown ``kraken`` → empty caps)
# is the one the snapshot pass must skip.
_CFGS = {
    "ib_paper": {
        "account_id": "ib_paper",
        "exchange": "interactive_brokers",
        "mode": "live",
    },
    "bybit_2": {
        "account_id": "bybit_2",
        "exchange": "bybit",
        "mode": "live",
    },
    "oanda_live": {
        "account_id": "oanda_live",
        "exchange": "oanda",
        "mode": "live",
    },
    # Alpaca has a per-symbol presence endpoint (GET /v2/positions/{symbol}), so
    # supports_position_presence(cfg) is True and the RISK-1 per-symbol confirm
    # gate engages: an absent-from-LIST row closes ONLY on a broker-confirmed
    # flat (account_position_present is False).
    "alpaca_live": {
        "account_id": "alpaca_live",
        "exchange": "alpaca",
        "mode": "live",
    },
    # Uncapped integration — exchange_management_caps("kraken") == frozenset(),
    # so account_supports_management(cfg, "open_positions") is False and the
    # snapshot pass leaves its rows as-is.
    "kraken_live": {
        "account_id": "kraken_live",
        "exchange": "kraken",
        "mode": "live",
    },
}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Tmp trade journal + stubbed account cfg loader + cleared confirm state."""
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    # 2-observation confirm with a 0-second time window: the second absent read
    # confirms immediately, so two back-to-back ticks close. The 2-observation
    # requirement (one tick arms, the next closes) still holds — this only drops
    # the wall-clock wait.
    monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", "0")
    # Local-PnL sweep is NOT invoked from the reconciler under test, but keep
    # the env clean so the row's pnl stays NULL (we assert that).
    monkeypatch.delenv("LOCAL_PNL_COMPUTE_DISABLED", raising=False)
    import src.runtime.order_monitor as _om
    _om._PENDING_SNAPSHOT_DISAPPEAR_CONFIRM.clear()
    _om._PENDING_ORPHAN_DISAPPEAR_CONFIRM.clear()
    _om._RESET_ALERT_LATCHED.clear()
    _om._RESET_SUSPECT_ROWS.clear()
    _om._RESET_SUSPECT_FLAGGED.clear()
    # No real Telegram / FCM from any reset alert path under test.
    monkeypatch.setattr(_om, "_send_reset_alert", lambda msg, caller: None)
    db = Database(db_path=str(db_path))

    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: _CFGS,
    )
    # Stub the orphan-adoption alert enqueue so tests don't write to the real
    # pending-pings dir.
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: None,
    )
    yield db


def _insert_open_trade(
    db, *, symbol, direction, account_id="ib_paper", strategy_name="mes_trend",
    size=2.0, entry=5300.0,
):
    """Insert a status='open' strategy-attributed trade row."""
    db.insert_trade({
        "timestamp": "2026-06-16T07:00:00+00:00",
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry,
        "position_size": size,
        "setup_type": strategy_name,
        "status": "open",
        "is_backtest": 0,
        "strategy_name": strategy_name,
        "account_id": account_id,
        "notes": "{}",
    })


def _ib_position(symbol="MES", side="long", size=2.0, entry=5300.0):
    """Shape ``account_open_positions`` returns for IB/Alpaca (side long/short)."""
    return {
        "symbol": symbol,
        "side": side,
        "size": size,
        "entry_price": entry,
        "unrealised_pnl": 0.0,
    }


def _trade_row(db, trade_id):
    conn = db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        return conn.execute(
            "SELECT * FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
    finally:
        conn.close()


def _only_open_trade_id(db, account_id="ib_paper"):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id FROM trades WHERE account_id=? ORDER BY id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def _status(db, trade_id):
    return _trade_row(db, trade_id)["status"]


# ─────────────────────────────────────────────────────────────────────
# Core: confirmed-absent-twice closes; absent-once arms
# ─────────────────────────────────────────────────────────────────────


def test_absent_once_arms_not_closed(tmp_db):
    """First absent observation ARMS the close-confirm — the row stays open."""
    _insert_open_trade(tmp_db, symbol="MES", direction="long")
    tid = _only_open_trade_id(tmp_db)
    # Snapshot is SUCCESSFUL ([] = no positions) but the row's (MES, long) is
    # absent. First pass arms, does not close.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["snapshot_pending"] == 1
    assert summary["snapshot_closed"] == 0
    assert _status(tmp_db, tid) == "open"


def test_absent_twice_closes(tmp_db):
    """Confirmed absent across TWO successful snapshots → row closed with
    exit_reason='exchange_flat_reconciled' and closed_by note; pnl stays NULL."""
    _insert_open_trade(tmp_db, symbol="MES", direction="long")
    tid = _only_open_trade_id(tmp_db)
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        s1 = _reconcile_orphan_exchange_positions(tmp_db)  # arms
        s2 = _reconcile_orphan_exchange_positions(tmp_db)  # confirms + closes
    assert s1["snapshot_pending"] == 1 and s1["snapshot_closed"] == 0
    assert s2["snapshot_closed"] == 1
    row = _trade_row(tmp_db, tid)
    assert row["status"] == "closed"
    assert row["exit_reason"] == "exchange_flat_reconciled"
    # PnL is left to the local-PnL sweep (mark-to-market) — NOT computed here.
    assert row["pnl"] is None
    note = json.loads(row["notes"])
    assert note["closed_by"] == "position_snapshot_reconciler"


def test_present_in_snapshot_not_closed_and_clears_pending(tmp_db):
    """A row present in the snapshot is never closed, and a recovered position
    CLEARS a prior absent arming (so a blip can't accumulate toward a close)."""
    _insert_open_trade(tmp_db, symbol="MES", direction="long")
    tid = _only_open_trade_id(tmp_db)
    import src.runtime.order_monitor as _om
    # Pass 1: absent → arms.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
    assert tid in _om._PENDING_SNAPSHOT_DISAPPEAR_CONFIRM
    # Pass 2: position present again → clears the arming, stays open.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_ib_position(symbol="MES", side="long")],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert tid not in _om._PENDING_SNAPSHOT_DISAPPEAR_CONFIRM
    assert summary["snapshot_closed"] == 0
    assert _status(tmp_db, tid) == "open"
    # Pass 3: absent again → must RE-ARM (not close), proving the clear reset
    # the 2-observation count.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        summary3 = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary3["snapshot_pending"] == 1
    assert summary3["snapshot_closed"] == 0
    assert _status(tmp_db, tid) == "open"


# ─────────────────────────────────────────────────────────────────────
# Fresh-fill grace: a too-young strategy trade is NOT snapshot-closed
# (BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE)
# ─────────────────────────────────────────────────────────────────────


def _insert_fresh_open_trade(
    db, *, symbol, direction, account_id="ib_paper", strategy_name="mes_trend",
):
    """Insert a status='open' row whose ``timestamp`` is NOW (so the fresh-fill
    grace sees it as just-opened)."""
    from datetime import datetime, timezone
    db.insert_trade({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "direction": direction,
        "entry_price": 5300.0,
        "position_size": 2.0,
        "setup_type": strategy_name,
        "status": "open",
        "is_backtest": 0,
        "strategy_name": strategy_name,
        "account_id": account_id,
        "notes": "{}",
    })


def test_too_young_trade_not_closed_even_when_absent_twice(tmp_db, monkeypatch):
    """A strategy-attributed trade younger than the fresh-fill grace is skipped
    by the snapshot-close pass — NOT armed, NOT closed — so a still-propagating
    Alpaca/IB fill (absent from the snapshot yet not flat) can't be false-closed
    and then re-adopted as an orphan (the IWM/alpaca_paper trade-2771 flap,
    BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE)."""
    monkeypatch.setenv("RECONCILER_SNAPSHOT_MIN_FILL_AGE_S", "300")
    _insert_fresh_open_trade(tmp_db, symbol="MES", direction="long")
    tid = _only_open_trade_id(tmp_db)
    import src.runtime.order_monitor as _om
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        s1 = _reconcile_orphan_exchange_positions(tmp_db)
        s2 = _reconcile_orphan_exchange_positions(tmp_db)
    assert s1["snapshot_too_young"] == 1 and s2["snapshot_too_young"] == 1
    assert s1["snapshot_closed"] == 0 and s2["snapshot_closed"] == 0
    # Never armed — a too-young row leaves no pending confirm state.
    assert tid not in _om._PENDING_SNAPSHOT_DISAPPEAR_CONFIRM
    assert _status(tmp_db, tid) == "open"


def test_grace_disabled_closes_young_trade(tmp_db, monkeypatch):
    """RECONCILER_SNAPSHOT_MIN_FILL_AGE_S=0 disables the age gate — the legacy
    2-observation-confirm behaviour (even a brand-new row closes when confirmed
    absent twice)."""
    monkeypatch.setenv("RECONCILER_SNAPSHOT_MIN_FILL_AGE_S", "0")
    _insert_fresh_open_trade(tmp_db, symbol="MES", direction="long")
    tid = _only_open_trade_id(tmp_db)
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)        # arms
        s2 = _reconcile_orphan_exchange_positions(tmp_db)   # confirms + closes
    assert s2["snapshot_closed"] == 1
    assert _status(tmp_db, tid) == "closed"


# ─────────────────────────────────────────────────────────────────────
# Safety: read failure never closes
# ─────────────────────────────────────────────────────────────────────


def test_read_failure_none_never_closes(tmp_db):
    """account_open_positions returning None (read failure) must NEVER close —
    even after arming on a prior successful-absent read, a None read leaves the
    row open and does not advance the confirm."""
    _insert_open_trade(tmp_db, symbol="MES", direction="long")
    tid = _only_open_trade_id(tmp_db)
    import src.runtime.order_monitor as _om
    # Pass 1: successful empty snapshot → arms.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
    assert tid in _om._PENDING_SNAPSHOT_DISAPPEAR_CONFIRM
    # Pass 2: read FAILS (None) → account skipped entirely; row stays open.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=None,
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["snapshot_closed"] == 0
    assert _status(tmp_db, tid) == "open"


def test_logged_out_ib_gateway_never_closes_open_row(tmp_db, monkeypatch):
    """END-TO-END: a logged-out-but-connected IB Gateway (empty positions()
    snapshot, net_liquidation None) must NOT false-close a genuinely-open IB
    row — even across TWO observations. This exercises the REAL
    ``account_open_positions`` (not a patched return) so the net_liq gate is
    proven to turn the ambiguous empty snapshot into a None read-failure, which
    the reconciler's ``if positions is None: continue`` guard then skips.
    """
    _insert_open_trade(tmp_db, symbol="MES", direction="long")
    tid = _only_open_trade_id(tmp_db)

    class _LoggedOutIBClient:
        """connected=true but logged out: positions()=[] and balance() has
        net_liquidation=None — the exact ambiguous wedge signature."""

        def positions(self):
            return []

        def balance(self):
            return {"net_liquidation": None}

        @property
        def connected(self):
            return True

    monkeypatch.setattr(
        "src.units.accounts.clients.ib_read_client_for",
        lambda account: _LoggedOutIBClient(),
    )

    # Two observations — a sustained (>confirm-window) logout. account_open_
    # positions returns None each time (net_liq gate), so the row never arms
    # toward a close and never closes.
    import src.runtime.order_monitor as _om
    s1 = _reconcile_orphan_exchange_positions(tmp_db)
    s2 = _reconcile_orphan_exchange_positions(tmp_db)

    assert s1["snapshot_closed"] == 0 and s1["snapshot_pending"] == 0
    assert s2["snapshot_closed"] == 0 and s2["snapshot_pending"] == 0
    assert tid not in _om._PENDING_SNAPSHOT_DISAPPEAR_CONFIRM
    assert _status(tmp_db, tid) == "open"  # genuinely-open row preserved


def test_logged_in_ib_gateway_flat_closes_open_row(tmp_db, monkeypatch):
    """Counter-case to the logged-out test: a VERIFIED logged-in IB Gateway
    (net_liquidation populated) that reports an empty positions() snapshot IS
    trustworthy "genuinely flat" — account_open_positions returns [] and the
    snapshot reconciler closes the absent row after the 2-observation confirm.
    Proves the net_liq gate doesn't over-block legitimate flat reconciliation.
    """
    _insert_open_trade(tmp_db, symbol="MES", direction="long")
    tid = _only_open_trade_id(tmp_db)

    class _LoggedInFlatIBClient:
        def positions(self):
            return []

        def balance(self):
            return {"net_liquidation": 10234.5}

    monkeypatch.setattr(
        "src.units.accounts.clients.ib_read_client_for",
        lambda account: _LoggedInFlatIBClient(),
    )

    s1 = _reconcile_orphan_exchange_positions(tmp_db)  # arms
    s2 = _reconcile_orphan_exchange_positions(tmp_db)  # confirms + closes
    assert s1["snapshot_pending"] == 1 and s1["snapshot_closed"] == 0
    assert s2["snapshot_closed"] == 1
    assert _status(tmp_db, tid) == "closed"


def test_read_failure_alone_never_closes(tmp_db):
    """A None snapshot on the very first observation does nothing — no arm, no
    close (the account-level None short-circuit skips it entirely)."""
    _insert_open_trade(tmp_db, symbol="MES", direction="long")
    tid = _only_open_trade_id(tmp_db)
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=None,
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["snapshot_closed"] == 0
    assert summary["snapshot_pending"] == 0
    assert _status(tmp_db, tid) == "open"


# ─────────────────────────────────────────────────────────────────────
# Scope: Bybit skipped; no-open_positions integration skipped
# ─────────────────────────────────────────────────────────────────────


def test_bybit_row_skipped_by_snapshot_pass(tmp_db):
    """Bybit declares the order_status management cap → the forward reconciler
    owns it. The snapshot pass must NOT close a Bybit row even when its
    (symbol, side) is absent from the snapshot."""
    _insert_open_trade(
        tmp_db, symbol="BTCUSDT", direction="long",
        account_id="bybit_2", strategy_name="vwap", size=0.003, entry=80000.0,
    )
    tid = _only_open_trade_id(tmp_db, account_id="bybit_2")
    # Empty Bybit snapshot twice — absent both times. Forward reconciler owns
    # Bybit closes; the snapshot pass must leave it open.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["snapshot_closed"] == 0
    assert summary["snapshot_pending"] == 0
    assert _status(tmp_db, tid) == "open"


def test_integration_without_open_positions_skipped(tmp_db):
    """An integration with no ``open_positions`` management cap (an unknown
    exchange → empty caps) can't be reconciled by snapshot — its DB-open rows
    are left as-is. (OANDA used to be this example; S2 wired its cap, so it is
    now reconciled like IB — see test_ltmgmt_oanda_wiring.py.)"""
    _insert_open_trade(
        tmp_db, symbol="XBTUSD", direction="long",
        account_id="kraken_live", strategy_name="fx_trend",
    )
    tid = _only_open_trade_id(tmp_db, account_id="kraken_live")
    # Even a successful [] snapshot must not close it because the cap gate
    # excludes an integration without the open_positions capability.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["snapshot_closed"] == 0
    assert summary["snapshot_pending"] == 0
    assert _status(tmp_db, tid) == "open"


# ─────────────────────────────────────────────────────────────────────
# orphan_adopt rows are owned by the close-on-disappear pass, not this one
# ─────────────────────────────────────────────────────────────────────


def test_orphan_adopt_row_not_double_handled(tmp_db):
    """An orphan_adopt row absent from the snapshot is closed by the existing
    close-on-disappear path (closed_disappeared), NOT counted as a
    snapshot_closed — the two paths don't double-handle the same row."""
    tmp_db.insert_trade({
        "timestamp": "2026-06-16T07:00:00+00:00",
        "symbol": "MES", "direction": "long", "entry_price": 5300.0,
        "position_size": 2.0, "setup_type": "adopted_orphan", "status": "open",
        "is_backtest": 0, "strategy_name": "orphan_adopt",
        "account_id": "ib_paper", "notes": "{}",
    })
    tid = _only_open_trade_id(tmp_db)
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    # Closed by the orphan_adopt close-on-disappear path, not the snapshot pass.
    assert summary["snapshot_closed"] == 0
    assert summary["closed_disappeared"] == 1
    row = _trade_row(tmp_db, tid)
    assert row["status"] == "closed"
    assert row["exit_reason"] == "adopted_orphan_disappeared"


# ─────────────────────────────────────────────────────────────────────
# Account-wide RESET detection (operator-requested 2026-07-08)
# ─────────────────────────────────────────────────────────────────────


def test_account_wide_reset_alerts_first_and_does_not_close(tmp_db, monkeypatch):
    """RISK-1 (BL-20260707-RECONCILER-MASS-FALSE-CLOSE): >= threshold positions on
    ONE account confirmed absent in a single pass is a SUSPECTED wholesale reset —
    which is NEVER auto-closed. Mass-closing N live rows on one inference is the
    amplifier that turned a bad 2026-07-07 read into 7 false closes. The rows stay
    OPEN, and ONE latched alert fires (not N closes)."""
    import src.runtime.order_monitor as _om
    alerts = []
    monkeypatch.setattr(
        _om, "_alert_account_reset",
        lambda aid, syms: alerts.append((aid, list(syms))),
    )
    for sym in ("MES", "MGC", "MHG", "SPY"):  # 4 >= threshold (3)
        _insert_open_trade(tmp_db, symbol=sym, direction="long")
    with patch(
        "src.units.accounts.clients.account_open_positions", return_value=[],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)            # arms all 4
        summary = _reconcile_orphan_exchange_positions(tmp_db)  # confirms → ALERTS
    # Nothing auto-closed; the mass vanish is alerted, not actioned.
    assert summary["snapshot_closed"] == 0
    assert summary["snapshot_reset_closed"] == 0
    assert summary["snapshot_reset_alerted"] == 4
    conn = tmp_db.connect()
    try:
        rows = conn.execute(
            "SELECT status FROM trades WHERE account_id='ib_paper'"
        ).fetchall()
    finally:
        conn.close()
    # All four rows stay OPEN on the detection pass (marked RESET-SUSPECT and
    # re-verified on later passes — see the NO-HALT section below).
    assert rows and all(r[0] == "open" for r in rows)
    assert len(_om._RESET_SUSPECT_ROWS) == 4
    # Exactly ONE consolidated alert for the account (not one per position).
    assert len(alerts) == 1
    assert alerts[0][0] == "ib_paper"
    assert set(alerts[0][1]) == {"MES", "MGC", "MHG", "SPY"}


def test_reset_alert_is_latched_not_re_fired_each_pass(tmp_db, monkeypatch):
    """The reset alert fires ONCE per account per episode (latched), not on every
    pass while the suspect rows are being re-verified (and resolved)."""
    import src.runtime.order_monitor as _om
    alerts = []
    monkeypatch.setattr(
        _om, "_alert_account_reset",
        lambda aid, syms: alerts.append((aid, list(syms))),
    )
    for sym in ("MES", "MGC", "MHG", "SPY"):
        _insert_open_trade(tmp_db, symbol=sym, direction="long")
    with patch(
        "src.units.accounts.clients.account_open_positions", return_value=[],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)   # arms
        _reconcile_orphan_exchange_positions(tmp_db)   # confirms → alert #1
        _reconcile_orphan_exchange_positions(tmp_db)   # re-verify flat #1
        _reconcile_orphan_exchange_positions(tmp_db)   # re-verify flat #2 → resolved
        _reconcile_orphan_exchange_positions(tmp_db)   # nothing left
    assert len(alerts) == 1  # latched — not re-fired


# ─────────────────────────────────────────────────────────────────────
# RISK-1: per-symbol positive-flat confirmation before an absence-close
# (BL-20260707-ALPACA-PAPER-NEGATIVE-EQUITY / -RECONCILER-MASS-FALSE-CLOSE)
# ─────────────────────────────────────────────────────────────────────


def test_alpaca_still_open_per_symbol_blocks_close(tmp_db, monkeypatch):
    """The batch snapshot reads the symbol as absent (partial/stale LIST — the
    exact 2026-07-07 signature), but the per-symbol broker check says STILL OPEN
    (True) → the row is NOT closed. This is the core RISK-1 false-close guard."""
    _insert_open_trade(tmp_db, symbol="SPY", direction="long",
                       account_id="alpaca_live", strategy_name="equity_mr")
    with patch(
        "src.units.accounts.clients.account_open_positions", return_value=[],
    ), patch(
        "src.units.accounts.clients.account_position_present", return_value=True,
    ):
        _reconcile_orphan_exchange_positions(tmp_db)            # arms
        summary = _reconcile_orphan_exchange_positions(tmp_db)  # would close…
    assert summary["snapshot_closed"] == 0
    assert summary["snapshot_presence_unconfirmed"] >= 1
    conn = tmp_db.connect()
    try:
        row = conn.execute(
            "SELECT status FROM trades WHERE account_id='alpaca_live'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "open"  # left open — never false-closed


def test_alpaca_per_symbol_404_confirms_close(tmp_db, monkeypatch):
    """When the per-symbol broker check CONFIRMS flat (False = a 404), an
    individual disappearance closes as exchange_flat_reconciled — a genuine
    strategy exit the batch LIST missed still reconciles."""
    _insert_open_trade(tmp_db, symbol="SPY", direction="long",
                       account_id="alpaca_live", strategy_name="equity_mr")
    with patch(
        "src.units.accounts.clients.account_open_positions", return_value=[],
    ), patch(
        "src.units.accounts.clients.account_position_present", return_value=False,
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["snapshot_closed"] == 1
    conn = tmp_db.connect()
    try:
        row = conn.execute(
            "SELECT status, exit_reason FROM trades WHERE account_id='alpaca_live'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "closed" and row[1] == "exchange_flat_reconciled"


def test_alpaca_per_symbol_readfail_does_not_close(tmp_db, monkeypatch):
    """A per-symbol read failure (None = couldn't confirm) is NEVER a close —
    only a broker-confirmed flat closes; an unconfirmed read waits."""
    _insert_open_trade(tmp_db, symbol="SPY", direction="long",
                       account_id="alpaca_live", strategy_name="equity_mr")
    with patch(
        "src.units.accounts.clients.account_open_positions", return_value=[],
    ), patch(
        "src.units.accounts.clients.account_position_present", return_value=None,
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["snapshot_closed"] == 0
    assert summary["snapshot_presence_unconfirmed"] >= 1
    conn = tmp_db.connect()
    try:
        row = conn.execute(
            "SELECT status FROM trades WHERE account_id='alpaca_live'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "open"


def test_alpaca_mass_vanish_all_confirmed_flat_still_alert_first(tmp_db, monkeypatch):
    """Even when EVERY symbol individually confirms flat (per-symbol 404), a
    mass vanish (>= threshold in one pass) is still alert-first, never a mass
    auto-close — the reset amplifier is disarmed for alpaca too."""
    import src.runtime.order_monitor as _om
    alerts = []
    monkeypatch.setattr(
        _om, "_alert_account_reset",
        lambda aid, syms: alerts.append((aid, list(syms))),
    )
    for sym in ("SPY", "QQQ", "IWM", "GLD"):  # 4 >= threshold (3)
        _insert_open_trade(tmp_db, symbol=sym, direction="long",
                           account_id="alpaca_live", strategy_name="equity_mr")
    with patch(
        "src.units.accounts.clients.account_open_positions", return_value=[],
    ), patch(
        "src.units.accounts.clients.account_position_present", return_value=False,
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["snapshot_closed"] == 0            # no mass auto-close
    assert summary["snapshot_reset_alerted"] == 4
    assert len(alerts) == 1
    conn = tmp_db.connect()
    try:
        rows = conn.execute(
            "SELECT status FROM trades WHERE account_id='alpaca_live'"
        ).fetchall()
    finally:
        conn.close()
    assert rows and all(r[0] == "open" for r in rows)  # left open


def test_below_threshold_is_normal_flat_no_reset_alert(tmp_db, monkeypatch):
    """< threshold absent positions close as individual
    exchange_flat_reconciled disappearances — NOT a reset, no reset alert."""
    import src.runtime.order_monitor as _om
    alerts = []
    monkeypatch.setattr(
        _om, "_alert_account_reset",
        lambda aid, syms: alerts.append((aid, list(syms))),
    )
    for sym in ("MES", "MGC"):  # 2 < threshold (3)
        _insert_open_trade(tmp_db, symbol=sym, direction="long")
    with patch(
        "src.units.accounts.clients.account_open_positions", return_value=[],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["snapshot_closed"] == 2
    assert summary.get("snapshot_reset_closed", 0) == 0
    conn = tmp_db.connect()
    try:
        rows = conn.execute(
            "SELECT exit_reason FROM trades WHERE account_id='ib_paper'"
        ).fetchall()
    finally:
        conn.close()
    assert rows and all(r[0] == "exchange_flat_reconciled" for r in rows)
    assert alerts == []


# ─────────────────────────────────────────────────────────────────────
# NO-HALT (operator directive 2026-10-09, "There is no halting."):
# RESET-SUSPECT rows are re-verified every pass and resolved automatically —
# never parked open for manual resolution (which held the strategy-monocle
# open-package gate shut for that strategy+symbol indefinitely).
# ─────────────────────────────────────────────────────────────────────

_RESET_SYMS = ("MES", "MGC", "MHG", "SPY")


def _spy_alerts(monkeypatch):
    import src.runtime.order_monitor as _om
    calls = {"detect": [], "flag": [], "recover": []}
    monkeypatch.setattr(_om, "_alert_account_reset",
                        lambda aid, syms: calls["detect"].append(aid))
    monkeypatch.setattr(_om, "_alert_reset_suspect_stuck",
                        lambda aid, items: calls["flag"].append((aid, items)))
    monkeypatch.setattr(_om, "_alert_reset_suspect_recovered",
                        lambda aid: calls["recover"].append(aid))
    return calls


def _statuses(db, account_id="ib_paper"):
    conn = db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        return [dict(r) for r in conn.execute(
            "SELECT id, symbol, status, exit_reason, pnl, exit_price, notes "
            "FROM trades WHERE account_id=? ORDER BY id", (account_id,),
        ).fetchall()]
    finally:
        conn.close()


def _seed_reset_rows(db, account_id="ib_paper", strategy="mes_trend",
                     with_packages=False):
    for i, sym in enumerate(_RESET_SYMS):
        pkg = f"pkg-reset-{i}" if with_packages else None
        if with_packages:
            db.insert_order_package({
                "order_package_id": pkg, "strategy_name": strategy,
                "symbol": sym, "direction": "long", "entry": 100.0,
                "sl": 90.0, "tp": 120.0, "confidence": 0.5, "status": "open",
                "linked_trade_id": None, "meta": {},
            })
        db.insert_trade({
            "timestamp": "2026-06-16T07:00:00+00:00", "symbol": sym,
            "direction": "long", "entry_price": 100.0, "position_size": 1.0,
            "setup_type": strategy, "status": "open", "is_backtest": 0,
            "strategy_name": strategy, "account_id": account_id,
            "notes": "{}", "order_package_id": pkg,
        })


def test_reset_suspect_auto_resolves_on_consecutive_flat_reads(tmp_db, monkeypatch):
    """Detection never closes; two further consecutive clean flat reads resolve
    every suspect row as exchange_reset_flat with PnL DECLARED unmeasured (no
    fabricated price), with no red flag on the normal path."""
    import src.runtime.order_monitor as _om
    from src.runtime.provenance import UNMEASURED_MARKER
    calls = _spy_alerts(monkeypatch)
    _seed_reset_rows(tmp_db)
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        _reconcile_orphan_exchange_positions(tmp_db)              # arm
        _reconcile_orphan_exchange_positions(tmp_db)              # detect → suspect
        assert all(r["status"] == "open" for r in _statuses(tmp_db))
        s3 = _reconcile_orphan_exchange_positions(tmp_db)         # flat read #1
        assert all(r["status"] == "open" for r in _statuses(tmp_db))
        assert s3["snapshot_reset_suspect"] == 4
        s4 = _reconcile_orphan_exchange_positions(tmp_db)         # flat read #2
    assert s4["snapshot_reset_resolved"] == 4
    rows = _statuses(tmp_db)
    assert len(rows) == 4
    for r in rows:
        assert r["status"] == "closed"
        assert r["exit_reason"] == "exchange_reset_flat"
        assert r["pnl"] is None and r["exit_price"] is None
        notes = json.loads(r["notes"])
        assert notes["pnl_source"] == UNMEASURED_MARKER
        assert notes["reset_event"] is True
    assert _om._RESET_SUSPECT_ROWS == {}
    assert calls["detect"] == ["ib_paper"]
    assert calls["flag"] == [] and calls["recover"] == []


def test_reset_resolved_rows_are_never_priced_by_the_local_sweep(tmp_db, monkeypatch):
    """The universal local-PnL sweep must not anchor-price a reset-resolved row:
    its closed_at is the re-verification time, not the exit time."""
    import src.runtime.order_monitor as _om
    _spy_alerts(monkeypatch)
    _seed_reset_rows(tmp_db)
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        for _ in range(4):
            _reconcile_orphan_exchange_positions(tmp_db)
    assert all(r["exit_reason"] == "exchange_reset_flat" for r in _statuses(tmp_db))
    summary = _om._sweep_local_pnl_for_unpriced(tmp_db)
    assert summary["scanned"] == 0
    assert all(r["pnl"] is None for r in _statuses(tmp_db))


def test_reset_suspect_kept_open_when_venue_shows_position(tmp_db, monkeypatch):
    """A suspect row the venue shows on a later pass stays OPEN and leaves the
    suspect state (managed normally) — never closed on the reset inference."""
    import src.runtime.order_monitor as _om
    calls = _spy_alerts(monkeypatch)
    _seed_reset_rows(tmp_db)
    present = [_ib_position(symbol=s, side="long", size=1.0, entry=100.0)
               for s in _RESET_SYMS]
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        _reconcile_orphan_exchange_positions(tmp_db)
        _reconcile_orphan_exchange_positions(tmp_db)             # suspect
    assert len(_om._RESET_SUSPECT_ROWS) == 4
    with patch("src.units.accounts.clients.account_open_positions",
               return_value=present):
        s = _reconcile_orphan_exchange_positions(tmp_db)
        _reconcile_orphan_exchange_positions(tmp_db)
    assert s["snapshot_reset_cleared_present"] == 4
    assert _om._RESET_SUSPECT_ROWS == {}
    assert all(r["status"] == "open" for r in _statuses(tmp_db))
    assert calls["flag"] == []


def test_reset_suspect_alpaca_per_symbol_open_keeps_row(tmp_db, monkeypatch):
    """Alpaca: batch list omits the symbols but the per-symbol check reads OPEN
    → the suspect row is kept open (partial-list guard still holds)."""
    import src.runtime.order_monitor as _om
    _spy_alerts(monkeypatch)
    _seed_reset_rows(tmp_db, account_id="alpaca_live", strategy="equity_mr")
    presence = {"v": False}
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]), \
         patch("src.units.accounts.clients.account_position_present",
               side_effect=lambda cfg, sym: presence["v"]):
        _reconcile_orphan_exchange_positions(tmp_db)
        _reconcile_orphan_exchange_positions(tmp_db)             # suspect
        presence["v"] = True
        _reconcile_orphan_exchange_positions(tmp_db)
    assert _om._RESET_SUSPECT_ROWS == {}
    assert all(r["status"] == "open"
               for r in _statuses(tmp_db, account_id="alpaca_live"))


def test_reset_suspect_read_failure_resolves_nothing_one_red_flag_then_recovers(
    tmp_db, monkeypatch,
):
    """Venue unreadable while suspect: nothing resolves, reconciliation keeps
    retrying, exactly ONE red flag after repeats; when reads come back flat the
    rows resolve on two consecutive clean reads and recovery is announced once."""
    import src.runtime.order_monitor as _om
    calls = _spy_alerts(monkeypatch)
    _seed_reset_rows(tmp_db)
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        _reconcile_orphan_exchange_positions(tmp_db)
        _reconcile_orphan_exchange_positions(tmp_db)             # suspect (pass 1)
    with patch("src.units.accounts.clients.account_open_positions", return_value=None):
        for _ in range(6):                                       # venue unreadable
            _reconcile_orphan_exchange_positions(tmp_db)
    assert all(r["status"] == "open" for r in _statuses(tmp_db))
    assert len(calls["flag"]) == 1                               # ONE red flag
    assert calls["flag"][0][0] == "ib_paper"
    assert len(_om._RESET_SUSPECT_ROWS) == 4                     # still trying
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        _reconcile_orphan_exchange_positions(tmp_db)             # clean flat #1
        assert all(r["status"] == "open" for r in _statuses(tmp_db))
        _reconcile_orphan_exchange_positions(tmp_db)             # clean flat #2
        _reconcile_orphan_exchange_positions(tmp_db)
    assert all(r["exit_reason"] == "exchange_reset_flat" for r in _statuses(tmp_db))
    assert len(calls["flag"]) == 1
    assert calls["recover"] == ["ib_paper"]


def test_reset_suspect_unconfirmed_per_symbol_never_resolves(tmp_db, monkeypatch):
    """Alpaca per-symbol read None (could not confirm) while suspect: never a
    close, the streak never builds, ONE red flag."""
    calls = _spy_alerts(monkeypatch)
    _seed_reset_rows(tmp_db, account_id="alpaca_live", strategy="equity_mr")
    presence = {"v": False}
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]), \
         patch("src.units.accounts.clients.account_position_present",
               side_effect=lambda cfg, sym: presence["v"]):
        _reconcile_orphan_exchange_positions(tmp_db)
        _reconcile_orphan_exchange_positions(tmp_db)             # suspect
        presence["v"] = None
        for _ in range(5):
            _reconcile_orphan_exchange_positions(tmp_db)
    assert all(r["status"] == "open"
               for r in _statuses(tmp_db, account_id="alpaca_live"))
    assert len(calls["flag"]) == 1


def test_reset_suspect_flat_reads_respect_confirm_spacing(tmp_db, monkeypatch):
    """Consecutive counted flat reads must be >= _close_confirm_seconds apart;
    a re-read inside the window is not counted, so it cannot resolve early."""
    _spy_alerts(monkeypatch)
    _seed_reset_rows(tmp_db)
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        _reconcile_orphan_exchange_positions(tmp_db)
        _reconcile_orphan_exchange_positions(tmp_db)             # suspect
        monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", "3600")
        _reconcile_orphan_exchange_positions(tmp_db)             # flat #1 counted
        _reconcile_orphan_exchange_positions(tmp_db)             # inside window
        _reconcile_orphan_exchange_positions(tmp_db)             # inside window
    assert all(r["status"] == "open" for r in _statuses(tmp_db))


def test_monocle_gate_unblocks_after_reset_suspect_resolves(tmp_db, monkeypatch):
    """The strategy-monocle open-package gate holds the strategy+symbol while
    the suspect row is open and clears ON ITS OWN once re-verification resolves
    it — no latch beyond the row/package state."""
    from src.runtime import strategy_monocle as sm
    _spy_alerts(monkeypatch)
    _seed_reset_rows(tmp_db, with_packages=True)
    scope = frozenset({"ib_paper"})
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        _reconcile_orphan_exchange_positions(tmp_db)
        _reconcile_orphan_exchange_positions(tmp_db)             # suspect
        assert sm._open_package_scope("mes_trend", "MES", scope)["action"] == "block"
        _reconcile_orphan_exchange_positions(tmp_db)
        _reconcile_orphan_exchange_positions(tmp_db)             # resolved
    for sym in _RESET_SYMS:
        gate = sm._open_package_scope("mes_trend", sym, scope)
        assert gate["action"] == "pass", (sym, gate)
        assert sm._has_open_package_for_strategy("mes_trend", sym) is None


# ─────────────────────────────────────────────────────────────────────
# Review hardening (PR #17256): false-flat span / populated-snapshot rule,
# trade-scoped protection cancel, restart persistence of RESET-SUSPECT.
# ─────────────────────────────────────────────────────────────────────

_STEP = 60  # seconds — the confirm interval these tests run at


def _install_clock(monkeypatch, confirm_seconds=_STEP):
    """Drive order_monitor's ``datetime.now`` from a test-controlled clock and
    set a non-zero confirm interval so the span rule has teeth."""
    from datetime import datetime as _real_dt
    from datetime import timezone as _tz

    import src.runtime.order_monitor as _om
    clock = {"t": _real_dt(2026, 10, 9, 12, 0, 0, tzinfo=_tz.utc)}

    class _FakeDT(_real_dt):
        @classmethod
        def now(cls, tz=None):
            return clock["t"] if tz is not None else clock["t"].replace(tzinfo=None)

    monkeypatch.setattr(_om, "datetime", _FakeDT)
    monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", str(confirm_seconds))
    return clock


def _tick(db, clock, seconds=_STEP):
    from datetime import timedelta as _td
    out = _reconcile_orphan_exchange_positions(db)
    clock["t"] = clock["t"] + _td(seconds=seconds)
    return out


def _open_syms(db, account_id):
    return {r["symbol"] for r in _statuses(db, account_id) if r["status"] == "open"}


def _span_ticks():
    import src.runtime.order_monitor as _om
    return _om._RESET_SUSPECT_MIN_SPAN_INTERVALS


@pytest.mark.parametrize("account_id", ["ib_paper", "oanda_live"])
def test_no_presence_venue_empty_snapshot_resolves_only_after_span(
    tmp_db, monkeypatch, account_id,
):
    """IB / OANDA (no per-symbol check), every read an EMPTY portfolio: the two
    consecutive clean reads are NOT enough — the streak must span
    _RESET_SUSPECT_MIN_SPAN_INTERVALS x the confirm interval. Waiting on the
    span is normal progress: no red flag."""
    import src.runtime.order_monitor as _om
    calls = _spy_alerts(monkeypatch)
    clock = _install_clock(monkeypatch)
    _seed_reset_rows(tmp_db, account_id=account_id)
    n = _span_ticks()
    assert n >= 5
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        _tick(tmp_db, clock)                       # arm
        _tick(tmp_db, clock)                       # detect → suspect
        # Counted flat reads #1 .. #n span (n-1) intervals — not yet enough,
        # even though flat_reads (n) >> _RESET_SUSPECT_CONFIRM_READS.
        for _ in range(n):
            _tick(tmp_db, clock)
        assert _open_syms(tmp_db, account_id) == set(_RESET_SYMS)
        assert len(_om._RESET_SUSPECT_ROWS) == 4
        s = _tick(tmp_db, clock)                   # read #n+1: span = n intervals
    assert s["snapshot_reset_resolved"] == 4
    assert all(r["exit_reason"] == "exchange_reset_flat"
               for r in _statuses(tmp_db, account_id))
    assert calls["flag"] == []


def test_no_presence_venue_populated_snapshot_resolves_without_span(
    tmp_db, monkeypatch,
):
    """OANDA: the confirming reads come from a POPULATED account snapshot (a
    still-open sibling position is listed) → the venue demonstrably answered
    with real book data, so two consecutive clean reads suffice."""
    _spy_alerts(monkeypatch)
    clock = _install_clock(monkeypatch)
    _seed_reset_rows(tmp_db, account_id="oanda_live", strategy="fx_trend")
    _insert_open_trade(tmp_db, symbol="EUR_USD", direction="long",
                       account_id="oanda_live", strategy_name="fx_trend")
    listed = [_ib_position(symbol="EUR_USD", side="long", size=2.0)]
    with patch("src.units.accounts.clients.account_open_positions",
               return_value=listed):
        _tick(tmp_db, clock)                       # arm
        _tick(tmp_db, clock)                       # detect
        _tick(tmp_db, clock)                       # flat #1 (populated)
        assert _open_syms(tmp_db, "oanda_live") >= set(_RESET_SYMS)
        s = _tick(tmp_db, clock)                   # flat #2 → resolved
    assert s["snapshot_reset_resolved"] == 4
    assert _open_syms(tmp_db, "oanda_live") == {"EUR_USD"}


def test_presence_venue_unchanged_resolves_on_confirm_reads(tmp_db, monkeypatch):
    """Alpaca (per-symbol 404 confirms every read) keeps the plain
    _RESET_SUSPECT_CONFIRM_READS rule — no span wait."""
    _spy_alerts(monkeypatch)
    clock = _install_clock(monkeypatch)
    _seed_reset_rows(tmp_db, account_id="alpaca_live", strategy="equity_mr")
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]), \
         patch("src.units.accounts.clients.account_position_present",
               return_value=False):
        _tick(tmp_db, clock)
        _tick(tmp_db, clock)                       # detect
        _tick(tmp_db, clock)                       # flat #1
        s = _tick(tmp_db, clock)                   # flat #2 → resolved
    assert s["snapshot_reset_resolved"] == 4


def test_span_streak_restarts_after_a_read_failure(tmp_db, monkeypatch):
    """A read failure mid-streak voids the span credit: the span is measured
    from the first clean read AFTER the failure."""
    _spy_alerts(monkeypatch)
    clock = _install_clock(monkeypatch)
    _seed_reset_rows(tmp_db)
    n = _span_ticks()
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        _tick(tmp_db, clock)
        _tick(tmp_db, clock)                       # detect
        for _ in range(n):                         # span n-1 intervals
            _tick(tmp_db, clock)
    with patch("src.units.accounts.clients.account_open_positions", return_value=None):
        _tick(tmp_db, clock)                       # failure → streak void
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        for _ in range(n):
            _tick(tmp_db, clock)
        assert _open_syms(tmp_db, "ib_paper") == set(_RESET_SYMS)
        _tick(tmp_db, clock)
    assert _open_syms(tmp_db, "ib_paper") == set()


def test_reset_resolve_protection_cancel_is_trade_scoped(tmp_db, monkeypatch):
    """The resolve cancels ONLY the resolved trade's own protection — the
    trade_id-scoped path with that row's sl/tp ids, size and direction — never
    the legacy symbol-wide sweep, and never a sibling trade's legs on the same
    symbol."""
    import src.runtime.order_monitor as _om
    _spy_alerts(monkeypatch)
    _seed_reset_rows(tmp_db)
    legs = {}
    for r in _statuses(tmp_db):
        legs[r["id"]] = (f"sl-{r['id']}", f"tp-{r['id']}")
        tmp_db.update_trade(r["id"], {"sl_order_id": f"sl-{r['id']}",
                                      "tp_order_id": f"tp-{r['id']}"})
    # Sibling on the SAME symbol (MES), still listed by the venue, with its own
    # resting legs that must never be touched.
    _insert_open_trade(tmp_db, symbol="MES", direction="short", size=3.0)
    sib_id = max(r["id"] for r in _statuses(tmp_db))
    tmp_db.update_trade(sib_id, {"sl_order_id": "sl-SIBLING",
                                 "tp_order_id": "tp-SIBLING"})

    calls = []
    real = _om._cancel_resting_protection_after_flat
    monkeypatch.setattr(
        _om, "_cancel_resting_protection_after_flat",
        lambda aid, sym, **kw: calls.append((aid, sym, kw)),
    )
    listed = [_ib_position(symbol="MES", side="short", size=3.0)]
    with patch("src.units.accounts.clients.account_open_positions",
               return_value=listed):
        for _ in range(4):
            _reconcile_orphan_exchange_positions(tmp_db)
    open_rows = [r for r in _statuses(tmp_db) if r["status"] == "open"]
    assert [r["id"] for r in open_rows] == [sib_id]
    resolved = [c for c in calls if c[2].get("trade_id") in legs]
    assert len(resolved) == 4
    for _aid, _sym, kw in calls:
        assert kw.get("trade_id") is not None, "symbol-wide sweep used"
        assert kw["trade_id"] != sib_id
        assert kw.get("sl_order_id") != "sl-SIBLING"
        assert kw.get("tp_order_id") != "tp-SIBLING"
    for _aid, _sym, kw in resolved:
        assert (kw["sl_order_id"], kw["tp_order_id"]) == legs[kw["trade_id"]]
        assert kw["row_qty"] == 1.0 and kw["row_direction"] == "long"
    # The sibling's size is passed as a SIBLING (so a size-scoped cancel
    # excludes it), never as the row's own size.
    mes = [kw for _, s, kw in resolved if s == "MES"]
    assert mes and mes[0]["sibling_qtys"] == [3.0]

    # End-to-end on an IB-shaped client: only the keyed per-trade group is
    # cancelled; the symbol-wide sweep is never called.
    monkeypatch.setattr(_om, "_cancel_resting_protection_after_flat", real)

    class _FakeIB:
        def __init__(self):
            self.trade_cancels, self.sweeps = [], []

        def cancel_trade_protection(self, symbol, trade_id):
            self.trade_cancels.append((symbol, trade_id))
            return {"retCode": 0, "result": {}}

        def cancel_resting_protection(self, symbol):
            self.sweeps.append(symbol)
            return {"retCode": 0}

    fake = _FakeIB()
    monkeypatch.setattr(_om, "_build_account_client",
                        lambda aid: (fake, _CFGS["ib_paper"]))
    first = resolved[0][2]["trade_id"]
    row = next(r for r in _statuses(tmp_db) if r["id"] == first)
    _om._cancel_closed_row_protection(
        {"id": first, "account_id": "ib_paper", "symbol": row["symbol"],
         "position_size": 1.0, "direction": "long"},
        tmp_db,
    )
    assert fake.trade_cancels == [(row["symbol"], first)]
    assert fake.sweeps == []


def test_reset_suspect_marker_persisted_and_cleared(tmp_db, monkeypatch):
    """Detection persists the RESET-SUSPECT marker in notes; a row the venue
    shows again has it cleared; a resolved row keeps it only as history."""
    import src.runtime.order_monitor as _om
    _spy_alerts(monkeypatch)
    _seed_reset_rows(tmp_db)
    key = _om._RESET_SUSPECT_NOTES_KEY
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        _reconcile_orphan_exchange_positions(tmp_db)
        _reconcile_orphan_exchange_positions(tmp_db)          # detect
    assert all(key in json.loads(r["notes"]) for r in _statuses(tmp_db))
    listed = [_ib_position(symbol="MES", side="long", size=1.0)]
    with patch("src.units.accounts.clients.account_open_positions",
               return_value=listed):
        _reconcile_orphan_exchange_positions(tmp_db)          # MES cleared
        _reconcile_orphan_exchange_positions(tmp_db)          # others resolve
    by_sym = {r["symbol"]: r for r in _statuses(tmp_db)}
    assert by_sym["MES"]["status"] == "open"
    assert key not in json.loads(by_sym["MES"]["notes"])
    for s in ("MGC", "MHG", "SPY"):
        n = json.loads(by_sym[s]["notes"])
        assert by_sym[s]["exit_reason"] == "exchange_reset_flat"
        assert key not in n and "reset_suspect_history" in n


def _simulate_restart():
    """Drop every piece of in-process reconciler state, as a restart does."""
    import src.runtime.order_monitor as _om
    _om._RESET_SUSPECT_ROWS.clear()
    _om._RESET_SUSPECT_FLAGGED.clear()
    _om._RESET_ALERT_LATCHED.clear()
    _om._PENDING_SNAPSHOT_DISAPPEAR_CONFIRM.clear()
    _om._PENDING_ORPHAN_DISAPPEAR_CONFIRM.clear()


def test_restart_rehydrates_suspect_and_restarts_span(tmp_db, monkeypatch):
    """A restart one interval short of the span requirement does NOT carry the
    span credit over: the rows are re-hydrated from notes with a fresh streak
    and need the FULL span again — a restart can only lengthen the wait."""
    import src.runtime.order_monitor as _om
    _spy_alerts(monkeypatch)
    clock = _install_clock(monkeypatch)
    _seed_reset_rows(tmp_db)
    n = _span_ticks()
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        _tick(tmp_db, clock)
        _tick(tmp_db, clock)                       # detect
        for _ in range(n):                         # one interval short
            _tick(tmp_db, clock)
        _simulate_restart()
        for _ in range(n):                         # fresh streak, n-1 intervals
            _tick(tmp_db, clock)
            assert _open_syms(tmp_db, "ib_paper") == set(_RESET_SYMS)
        assert all(st["last_read"] != "detected"
                   for st in _om._RESET_SUSPECT_ROWS.values())
        _tick(tmp_db, clock)                       # full span post-restart
    assert all(r["exit_reason"] == "exchange_reset_flat" for r in _statuses(tmp_db))


def test_restart_never_routes_suspect_rows_to_individual_close(tmp_db, monkeypatch):
    """After a restart only two suspect rows remain — below the reset
    threshold. Without the persisted marker they would arm → confirm and close
    on the weaker individual 2-observation path (exchange_flat_reconciled) one
    interval after restart. With it they stay RESET-SUSPECT, need the full
    post-restart confirm-read streak, and close only as exchange_reset_flat."""
    _spy_alerts(monkeypatch)
    clock = _install_clock(monkeypatch)
    _seed_reset_rows(tmp_db)
    with patch("src.units.accounts.clients.account_open_positions", return_value=[]):
        _tick(tmp_db, clock)
        _tick(tmp_db, clock)                       # detect all 4
    # MES + MGC come back on the venue (cleared, kept open); MHG + SPY remain
    # suspect.
    listed = [_ib_position(symbol=s, side="long", size=1.0) for s in ("MES", "MGC")]
    with patch("src.units.accounts.clients.account_open_positions",
               return_value=listed):
        _tick(tmp_db, clock)                       # MES/MGC cleared; others flat #1
        _simulate_restart()
        _tick(tmp_db, clock, seconds=1)            # re-hydrated, fresh flat #1
        _tick(tmp_db, clock, seconds=_STEP - 1)    # inside window: not counted
        assert _open_syms(tmp_db, "ib_paper") == set(_RESET_SYMS)
        _tick(tmp_db, clock)                       # flat #2 (populated) → resolves
    by_sym = {r["symbol"]: r for r in _statuses(tmp_db)}
    assert by_sym["MES"]["status"] == "open" and by_sym["MGC"]["status"] == "open"
    for s in ("MHG", "SPY"):
        assert by_sym[s]["exit_reason"] == "exchange_reset_flat", by_sym[s]
