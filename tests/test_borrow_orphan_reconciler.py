"""S-055 — standalone borrow-orphan reconciler.

Pins the contract for
``src.runtime.order_monitor._reconcile_orphan_borrows`` — the
operator-confirmed fail-safe (2026-05-08, Tier-2) for spot-margin
accounts that carry outstanding ``borrowAmount > 0`` with no DB-open
trade backing it. Causes seen in the wild:

  * Trade fired and closed across a process restart; Bybit's
    auto-repay didn't fully clear the borrow.
  * Mid-config-change partial fill leaves a stub borrow line.
  * The post-close repay verify (in ``close_open_position``)
    failed transiently; this is the catch-up path.

The reconciler runs every monitor tick (same gate as
``_reconcile_open_trades``: ``MONITOR_RECONCILE_ENABLED``) and
respects the same grace window (``RECONCILER_GRACE_SECONDS``,
default 60 s — PR #501) so a freshly-placed trade isn't repaid out
from under the matching engine.

Five contracts under test:

1. Empty wallet (no borrows) → no-op.
2. Spot-margin account with borrow + no open trade + outside grace
   → repay fires, audit row written.
3. Spot-margin account with borrow + matching open trade → skip
   (the open trade backs the borrow).
4. Spot-margin account with borrow + freshly-placed trade (within
   grace window) → skip (race-protection).
5. Cash-spot account with anomalous borrow rows → skip (the
   reconciler is spot-margin-only).
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# pipeline.py needs matplotlib at import time via signal_notifications.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

from src.runtime.order_monitor import _reconcile_orphan_borrows
from src.units.db.database import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _insert_trade(
    db,
    *,
    account_id="bybit_2",
    symbol="BTCUSDT",
    direction="short",
    status="open",
    age_seconds=3600,
):
    """Insert a trade row aged *age_seconds* into the past."""
    created_at = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).isoformat()
    db.insert_trade({
        "timestamp": "2026-05-08T08:00:00+00:00",
        "symbol": symbol,
        "direction": direction,
        "entry_price": 80_000.0,
        "stop_loss": 80_500.0,
        "take_profit_1": 79_500.0,
        "position_size": 0.001,
        "setup_type": "vwap",
        "entry_reason": "vwap signal",
        "status": status,
        "is_backtest": 0,
        "strategy_name": "vwap",
        "account_id": account_id,
        "notes": json.dumps({"trade_id": "stub"}),
        "created_at": created_at,
    })


def _wallet_with(coins):
    return {"result": {"list": [{"coin": list(coins)}]}}


class _RepayClient:
    """Captures repay calls, scripts wallet response."""

    def __init__(self, *, wallet_response, repay_response=None):
        self._wallet = wallet_response
        self.repay_calls = []
        self._repay_response = repay_response or {"retCode": 0, "retMsg": "OK"}

    def get_wallet_balance(self, **_):
        return self._wallet

    def repay(self, **kwargs):
        self.repay_calls.append(kwargs)
        return self._repay_response


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Tmp trade journal + reconciler env wired on. Mirrors the
    fixture shape in ``tests/test_monitor_reconciler.py`` so the two
    suites are read side-by-side.
    """
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")

    db = Database(db_path=str(db_path))

    cfgs = {
        "bybit_2": {
            "account_id": "bybit_2",
            "exchange": "bybit",
            "api_key_env": "BYBIT_KEY_2",
            "api_secret_env": None,
            "mode": "live",
            "market_type": "spot-margin",
        },
        "bybit_1": {
            "account_id": "bybit_1",
            "exchange": "bybit",
            "api_key_env": "BYBIT_KEY_1",
            "api_secret_env": None,
            "mode": "live",
            "market_type": "spot",
        },
    }
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: cfgs,
    )
    return db


# ---------------------------------------------------------------------------
# Contract 1 — empty wallet, no borrows → no-op
# ---------------------------------------------------------------------------


def test_no_borrow_outstanding_is_noop(tmp_db, monkeypatch):
    client = _RepayClient(wallet_response=_wallet_with([
        {"coin": "USDT", "walletBalance": "100", "borrowAmount": "0"},
        {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    summary = _reconcile_orphan_borrows(tmp_db)
    assert summary["repaid"] == 0
    assert summary["errors"] == 0
    assert client.repay_calls == []


# ---------------------------------------------------------------------------
# Contract 2 — orphan borrow + no open trade → repay fires
# ---------------------------------------------------------------------------


def test_orphan_borrow_repaid_and_audit_emitted(tmp_db, monkeypatch):
    """The headline contract. Wallet has BTC borrow > epsilon, the DB
    has no open trade for bybit_2 → the reconciler calls repay and
    emits a ``borrow_orphan_repaid`` audit row.
    """
    client = _RepayClient(wallet_response=_wallet_with([
        {"coin": "USDT", "walletBalance": "85", "borrowAmount": "0"},
        {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0.001"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    captured_audit = []
    monkeypatch.setattr(
        "src.utils.signal_audit_logger.log_signal",
        lambda payload: captured_audit.append(payload),
    )
    summary = _reconcile_orphan_borrows(tmp_db)

    assert summary["checked"] == 1
    assert summary["repaid"] == 1
    assert summary["errors"] == 0
    assert client.repay_calls == [{"coin": "BTC", "qty": "0.001"}]

    # Audit row pinned: action + status + coin + qty + reason.
    assert len(captured_audit) == 1
    payload = captured_audit[0]
    assert payload["action"] == "borrow_orphan_repaid"
    assert payload["status"] == "ok"
    assert payload["coin"] == "BTC"
    assert payload["qty"] == pytest.approx(0.001)
    assert payload["account_id"] == "bybit_2"
    assert "no DB-open trade" in payload["reason"]


# ---------------------------------------------------------------------------
# Contract 3 — borrow + matching open trade → skip
# ---------------------------------------------------------------------------


def test_open_short_trade_protects_btc_borrow(tmp_db, monkeypatch):
    """A live BTC borrow is the spot-margin equivalent of a
    short-on-BTCUSDT. If the DB has the matching ``status='open' +
    direction='short' + symbol like 'BTC%'`` row, the reconciler
    must NOT repay — that borrow is what underwrites the open
    position.
    """
    _insert_trade(
        tmp_db, account_id="bybit_2",
        symbol="BTCUSDT", direction="short",
        status="open", age_seconds=3600,
    )
    client = _RepayClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0.001"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    summary = _reconcile_orphan_borrows(tmp_db)
    assert summary["repaid"] == 0
    assert summary["skipped_holding_trade"] == 1
    assert client.repay_calls == []


def test_open_long_trade_protects_usdt_borrow(tmp_db, monkeypatch):
    """USDT borrows underwrite the long side. Symmetric protection
    against repaying a live leveraged-long's borrow.
    """
    _insert_trade(
        tmp_db, account_id="bybit_2",
        symbol="BTCUSDT", direction="long",
        status="open", age_seconds=3600,
    )
    client = _RepayClient(wallet_response=_wallet_with([
        {"coin": "USDT", "walletBalance": "120", "borrowAmount": "30"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    summary = _reconcile_orphan_borrows(tmp_db)
    assert summary["repaid"] == 0
    assert summary["skipped_holding_trade"] == 1
    assert client.repay_calls == []


# ---------------------------------------------------------------------------
# Contract 4 — fresh trade (within grace window) → skip
# ---------------------------------------------------------------------------


def test_freshly_placed_trade_blocks_repay(tmp_db, monkeypatch):
    """A trade placed 5 s ago is younger than the 60 s grace window
    (PR #501). Even if the trade row's ``status`` doesn't yet show
    open in the DB read (e.g. orphan-stamping race), the recent
    timestamp must shield the borrow from being repaid out.
    """
    # Trade is closed already, but its created_at is fresh — typical
    # of a "just placed and very-fast-closed" scenario where Bybit
    # might lag in clearing the borrow.
    _insert_trade(
        tmp_db, account_id="bybit_2",
        symbol="BTCUSDT", direction="short",
        status="closed", age_seconds=5,
    )
    client = _RepayClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0.001"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    summary = _reconcile_orphan_borrows(tmp_db)
    assert summary["repaid"] == 0
    assert summary["skipped_recent"] == 1
    assert client.repay_calls == []


def test_old_closed_trade_does_not_block_repay(tmp_db, monkeypatch):
    """A closed trade older than the grace window is NOT a guardian
    of a live borrow. If borrowAmount is still non-zero with no open
    trade and no recent activity, the reconciler repays.
    """
    _insert_trade(
        tmp_db, account_id="bybit_2",
        symbol="BTCUSDT", direction="short",
        status="closed", age_seconds=3600,
    )
    client = _RepayClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0.001"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    summary = _reconcile_orphan_borrows(tmp_db)
    assert summary["repaid"] == 1
    assert client.repay_calls == [{"coin": "BTC", "qty": "0.001"}]


# ---------------------------------------------------------------------------
# Contract 5 — cash-spot accounts are skipped entirely
# ---------------------------------------------------------------------------


def test_cash_spot_account_is_skipped(tmp_db, monkeypatch):
    """The reconciler must filter to ``market_type: spot-margin`` —
    a cash-spot account doesn't have a Spot Margin surface, so
    ``client.repay`` would 401 every minute. Even if the wallet
    response carries an anomalous borrow row, leave it alone.
    """
    # Override the cfg map so only a cash-spot account is present.
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {
            "bybit_1": {
                "account_id": "bybit_1",
                "exchange": "bybit",
                "api_key_env": "BYBIT_KEY_1",
                "api_secret_env": None,
                "mode": "live",
                "market_type": "spot",
            },
        },
    )
    client = _RepayClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0.5"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    summary = _reconcile_orphan_borrows(tmp_db)
    # checked counter is incremented only after the spot-margin
    # filter passes; cash-spot accounts never increment.
    assert summary["checked"] == 0
    assert summary["repaid"] == 0
    assert client.repay_calls == []


# ---------------------------------------------------------------------------
# Auxiliary: gate flag respected
# ---------------------------------------------------------------------------


def test_disabled_gate_is_noop(tmp_db, monkeypatch):
    """``MONITOR_RECONCILE_ENABLED=false`` must short-circuit the
    whole sweep. Same gate as ``_reconcile_open_trades``.
    """
    monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "false")
    client = _RepayClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0.001"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    summary = _reconcile_orphan_borrows(tmp_db)
    assert summary == {
        "checked": 0,
        "repaid": 0,
        "skipped_recent": 0,
        "skipped_no_creds": 0,
        "skipped_holding_trade": 0,
        "errors": 0,
    }
    assert client.repay_calls == []


def test_dry_run_account_is_skipped(tmp_db, monkeypatch):
    """Dry-run accounts have no real exchange to repay against.
    Mirror the dry-run skip in ``_reconcile_open_trades``.
    """
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {
            "bybit_2": {
                "account_id": "bybit_2",
                "exchange": "bybit",
                "api_key_env": "BYBIT_KEY_2",
                "api_secret_env": None,
                "mode": "dry_run",
                "market_type": "spot-margin",
            },
        },
    )
    client = _RepayClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0.001"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    summary = _reconcile_orphan_borrows(tmp_db)
    assert summary["checked"] == 0
    assert summary["repaid"] == 0
    assert client.repay_calls == []


def test_repay_failure_recorded_as_error(tmp_db, monkeypatch):
    """A non-zero retCode from Bybit (e.g. transient infra glitch)
    must increment ``errors``, not ``repaid``, AND emit an audit row
    so the operator sees the failure trail. The next sweep retries.
    """
    client = _RepayClient(
        wallet_response=_wallet_with([
            {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0.001"},
        ]),
        repay_response={"retCode": 10002, "retMsg": "Server unavailable"},
    )
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    captured_audit = []
    monkeypatch.setattr(
        "src.utils.signal_audit_logger.log_signal",
        lambda payload: captured_audit.append(payload),
    )
    summary = _reconcile_orphan_borrows(tmp_db)
    assert summary["repaid"] == 0
    assert summary["errors"] == 1
    assert len(captured_audit) == 1
    assert captured_audit[0]["status"] == "failed"
    assert "Server unavailable" in (captured_audit[0]["error"] or "")
