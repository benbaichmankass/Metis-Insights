"""S-060 — standalone orphan-position reconciler.

Pins the contract for
``src.runtime.order_monitor._reconcile_orphan_positions`` — the
companion to S-055 for the **position leg** of an orphaned trade.

Where S-055 catches a stranded ``borrowAmount`` (the short leg),
S-060 catches a stranded non-USDT ``walletBalance`` (the long leg).
The canonical scenario (verified live 2026-05-09): the stuck-strategy
watchdog force-clears a vwap LONG package after 30 min, the linked
trade row goes to ``orphaned``, but the BTC the leveraged buy
purchased stays in the spot wallet — capital sunk into stranded
inventory until something sells it. S-060 is that something.

Contracts under test mirror the S-055 suite shape so they read
side-by-side:

1. Empty wallet (no non-USDT balance) → no-op.
2. Orphan position (base coin > epsilon, no open long, outside
   grace) → sell fires + audit row emitted.
3. Open long on the same symbol → skip (the long backs the
   balance).
4. Freshly-placed trade within grace window → skip
   (race-protection identical to S-055).
5. Cash-spot account is skipped (the reconciler is spot-margin
   only — cash spot's coin holdings are operator-deposited
   inventory, not orphans).
6. Disabled gate (``MONITOR_RECONCILE_ENABLED=false``) → no-op.
7. Dry-run account is skipped.
8. Sell failure is recorded as ``errors++`` with a failed-status
   audit row.
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

# pipeline.py needs matplotlib at import time via signal_notifications.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

from src.runtime.order_monitor import _reconcile_orphan_positions
from src.units.db.database import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _insert_trade(
    db,
    *,
    account_id="bybit_2",
    symbol="BTCUSDT",
    direction="long",
    status="open",
    age_seconds=3600,
):
    """Insert a trade row aged *age_seconds* into the past."""
    created_at = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).isoformat()
    db.insert_trade({
        "timestamp": "2026-05-09T08:00:00+00:00",
        "symbol": symbol,
        "direction": direction,
        "entry_price": 80_000.0,
        "stop_loss": 79_500.0,
        "take_profit_1": 80_500.0,
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


class _SellClient:
    """Stub Bybit client. Captures wallet-read calls; the actual
    ``close_open_position`` call is patched at module scope so this
    client only needs to script ``get_wallet_balance``.
    """

    def __init__(self, *, wallet_response):
        self._wallet = wallet_response

    def get_wallet_balance(self, **_):
        return self._wallet


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Tmp trade journal + reconciler env wired on. Mirrors the
    fixture in tests/test_borrow_orphan_reconciler.py.
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
        "bybit_dry": {
            "account_id": "bybit_dry",
            "exchange": "bybit",
            "api_key_env": "BYBIT_KEY_DRY",
            "api_secret_env": None,
            "mode": "dry_run",
            "market_type": "spot-margin",
        },
    }
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: cfgs,
    )
    return db


# ---------------------------------------------------------------------------
# Contract 1 — empty wallet, no non-USDT balance → no-op
# ---------------------------------------------------------------------------


def test_no_position_residue_is_noop(tmp_db, monkeypatch):
    client = _SellClient(wallet_response=_wallet_with([
        {"coin": "USDT", "walletBalance": "100", "borrowAmount": "0"},
        {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    sell_calls = []
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda *a, **kw: (sell_calls.append((a, kw)), {"ok": True})[1],
    )
    summary = _reconcile_orphan_positions(tmp_db)
    assert summary["sold"] == 0
    assert summary["errors"] == 0
    assert sell_calls == []


# ---------------------------------------------------------------------------
# Contract 2 — orphan position + no open long → sell fires
# ---------------------------------------------------------------------------


def test_orphan_position_sold_and_audit_emitted(tmp_db, monkeypatch):
    """Headline contract for today's #582 incident.

    Wallet has 0.001 BTC sitting from a watchdog-orphaned vwap long.
    No DB-open long backs it. Reconciler markets sells the BTC back
    to USDT and emits a ``position_orphan_liquidated`` audit row.
    """
    client = _SellClient(wallet_response=_wallet_with([
        {"coin": "USDT", "walletBalance": "85", "borrowAmount": "0"},
        {"coin": "BTC", "walletBalance": "0.001", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    sell_calls = []

    def _fake_close(client_arg, cfg_arg, *, symbol, side, qty):
        sell_calls.append({
            "symbol": symbol, "side": side, "qty": qty,
            "account_id": cfg_arg.get("account_id"),
        })
        return {"ok": True, "exchange_order_id": "stub-001", "error": None}

    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        _fake_close,
    )
    captured_audit = []
    monkeypatch.setattr(
        "src.utils.signal_audit_logger.log_signal",
        lambda payload: captured_audit.append(payload),
    )

    summary = _reconcile_orphan_positions(tmp_db)

    assert summary["checked"] == 1
    assert summary["sold"] == 1
    assert summary["errors"] == 0
    assert sell_calls == [{
        "symbol": "BTCUSDT",
        "side": "long",
        "qty": pytest.approx(0.001),
        "account_id": "bybit_2",
    }]

    assert len(captured_audit) == 1
    payload = captured_audit[0]
    assert payload["action"] == "position_orphan_liquidated"
    assert payload["status"] == "ok"
    assert payload["coin"] == "BTC"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["qty"] == pytest.approx(0.001)
    assert payload["account_id"] == "bybit_2"
    assert "no DB-open long" in payload["reason"]


# ---------------------------------------------------------------------------
# Contract 3 — open long protects matching base-coin balance → skip
# ---------------------------------------------------------------------------


def test_open_long_trade_protects_btc_balance(tmp_db, monkeypatch):
    """A live ``walletBalance(BTC) > 0`` IS the spot-margin
    equivalent of a long-on-BTCUSDT. If the DB has the matching
    ``status='open' + direction='long' + symbol like 'BTC%'`` row,
    the reconciler must NOT sell — the balance is what the trade
    holds.
    """
    _insert_trade(
        tmp_db, account_id="bybit_2",
        symbol="BTCUSDT", direction="long",
        status="open", age_seconds=3600,
    )
    client = _SellClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0.001", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    sell_calls = []
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda *a, **kw: (sell_calls.append((a, kw)), {"ok": True})[1],
    )
    summary = _reconcile_orphan_positions(tmp_db)
    assert summary["sold"] == 0
    assert summary["skipped_holding_trade"] == 1
    assert sell_calls == []


def test_open_short_trade_does_not_protect_btc_balance(tmp_db, monkeypatch):
    """Defence boundary: an open SHORT on BTCUSDT does NOT
    underwrite a BTC ``walletBalance > 0`` (a short borrows BTC and
    sells it; it doesn't hold BTC). So a stranded BTC balance with
    only a short open is still an orphan and gets liquidated.
    """
    _insert_trade(
        tmp_db, account_id="bybit_2",
        symbol="BTCUSDT", direction="short",
        status="open", age_seconds=3600,
    )
    client = _SellClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0.001", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    sell_calls = []
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda *a, **kw: (
            sell_calls.append((a, kw)),
            {"ok": True, "exchange_order_id": "x"},
        )[1],
    )
    summary = _reconcile_orphan_positions(tmp_db)
    assert summary["sold"] == 1
    assert summary["skipped_holding_trade"] == 0
    assert len(sell_calls) == 1


# ---------------------------------------------------------------------------
# Contract 4 — fresh trade (within grace window) → skip
# ---------------------------------------------------------------------------


def test_freshly_placed_trade_blocks_sell(tmp_db, monkeypatch):
    """A trade placed 5 s ago is younger than the 60 s grace window
    (PR #501; same window as S-055). Even if the trade ended up
    closed already, the recent timestamp must shield the matching
    base-coin balance from being sold out — the matching engine
    might still be settling.
    """
    _insert_trade(
        tmp_db, account_id="bybit_2",
        symbol="BTCUSDT", direction="long",
        status="closed", age_seconds=5,
    )
    client = _SellClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0.001", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    sell_calls = []
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda *a, **kw: (sell_calls.append((a, kw)), {"ok": True})[1],
    )
    summary = _reconcile_orphan_positions(tmp_db)
    assert summary["sold"] == 0
    assert summary["skipped_recent"] == 1
    assert sell_calls == []


# ---------------------------------------------------------------------------
# Contract 5 — cash-spot account is skipped
# ---------------------------------------------------------------------------


def test_cash_spot_account_is_skipped(tmp_db, monkeypatch):
    """``bybit_1`` is a cash-spot account (``market_type: spot``).
    Its BTC holdings are operator-deposited inventory that
    turtle_soup sizes against — never an orphan. The reconciler
    must skip cash-spot accounts via ``_is_spot_margin_cfg``
    BEFORE the wallet read.

    Asserted via ``checked == 1``: only the spot-margin account
    (bybit_2) ever passes the filter. bybit_1 and bybit_dry never
    reach the wallet-read code path.
    """
    # Empty wallet so bybit_2 itself doesn't trigger a sell — we're
    # isolating the ``which accounts get checked`` contract.
    client = _SellClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    sell_calls = []
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda *a, **kw: (sell_calls.append((a, kw)), {"ok": True})[1],
    )
    summary = _reconcile_orphan_positions(tmp_db)
    # Only bybit_2 (spot-margin) was checked. bybit_1 (spot) and
    # bybit_dry (dry mode) are filtered before the wallet read.
    assert summary["checked"] == 1
    assert summary["sold"] == 0
    assert sell_calls == []


# ---------------------------------------------------------------------------
# Contract 6 — disabled gate → no-op
# ---------------------------------------------------------------------------


def test_disabled_gate_is_noop(tmp_db, monkeypatch):
    monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "false")
    client = _SellClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0.001", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    sell_calls = []
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda *a, **kw: (sell_calls.append((a, kw)), {"ok": True})[1],
    )
    summary = _reconcile_orphan_positions(tmp_db)
    assert summary == {
        "checked": 0, "sold": 0, "skipped_recent": 0,
        "skipped_no_creds": 0, "skipped_holding_trade": 0, "errors": 0,
    }
    assert sell_calls == []


# ---------------------------------------------------------------------------
# Contract 7 — dry-run account is skipped
# ---------------------------------------------------------------------------


def test_dry_run_account_is_skipped(tmp_db, monkeypatch):
    """``bybit_dry`` is mode=dry_run. The reconciler must not touch
    a dry-run wallet — even if a real residue showed up (manual
    deposit, env-var swap), the operator's intent is no live
    interaction. Same isolation pattern as
    ``test_cash_spot_account_is_skipped``: empty wallet on bybit_2
    so the ``checked`` counter is the clean signal that the gate
    fires before the wallet read.
    """
    client = _SellClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    sell_calls = []
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda *a, **kw: (sell_calls.append((a, kw)), {"ok": True})[1],
    )
    summary = _reconcile_orphan_positions(tmp_db)
    # Only bybit_2 was checked; bybit_dry is gated out before
    # the wallet read.
    assert summary["checked"] == 1
    assert sell_calls == []


# ---------------------------------------------------------------------------
# Contract 8 — sell failure recorded as error
# ---------------------------------------------------------------------------


def test_qty_floored_to_base_precision(tmp_db, monkeypatch):
    """S-060 follow-up (verified live 2026-05-09): the
    ``walletBalance`` field carries 8 decimals (post-fee fractional
    dust), but Bybit V5 spot rejects qty with > 6 decimals as
    ``retCode 170137``. The reconciler must floor to
    ``_SPOT_BASE_PRECISION`` before submitting.

    Concrete case from the live error trace: walletBalance =
    0.00230135 → sell qty must be 0.002301 (6 decimals, not 8).
    """
    client = _SellClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0.00230135", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )

    captured_qty = []

    def _fake_close(client_arg, cfg_arg, *, symbol, side, qty):
        captured_qty.append(qty)
        return {"ok": True, "exchange_order_id": "stub", "error": None}

    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        _fake_close,
    )
    captured_audit = []
    monkeypatch.setattr(
        "src.utils.signal_audit_logger.log_signal",
        lambda payload: captured_audit.append(payload),
    )

    summary = _reconcile_orphan_positions(tmp_db)
    assert summary["sold"] == 1
    # Floored to 6 decimals — the trailing 35 of 0.00230135 dropped.
    assert captured_qty == [pytest.approx(0.002301, abs=1e-9)]
    # Audit row carries the floored qty (what was actually submitted),
    # not the raw wallet balance — the operator wants to see the real
    # action.
    assert captured_audit[0]["qty"] == pytest.approx(0.002301, abs=1e-9)


def test_dust_below_base_precision_is_skipped(tmp_db, monkeypatch):
    """If the wallet has only sub-step dust (e.g. 5e-7 BTC), flooring
    to 6 decimals collapses it to 0. The reconciler must skip rather
    than spin on a guaranteed Bybit rejection every tick.
    """
    client = _SellClient(wallet_response=_wallet_with([
        # 5e-7 BTC: above _BORROW_REPAY_EPSILON (1e-6), but rounds
        # to zero at 6-decimal precision.
        {"coin": "BTC", "walletBalance": "0.00000050", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )
    sell_calls = []
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda *a, **kw: (
            sell_calls.append((a, kw)),
            {"ok": True},
        )[1],
    )
    summary = _reconcile_orphan_positions(tmp_db)
    assert summary["sold"] == 0
    assert summary["errors"] == 0
    assert sell_calls == []


def test_sell_failure_recorded_as_error(tmp_db, monkeypatch):
    """When ``close_open_position`` returns ``ok=False`` (Bybit
    rejected the order — below min size, market closed, etc.) the
    reconciler bumps ``errors`` and emits a failed-status audit
    row so the operator sees the retry trail.
    """
    client = _SellClient(wallet_response=_wallet_with([
        {"coin": "BTC", "walletBalance": "0.001", "borrowAmount": "0"},
    ]))
    monkeypatch.setattr(
        "src.runtime.order_monitor._build_client_for_cfg",
        lambda cfg: client,
    )

    def _fake_close(client_arg, cfg_arg, *, symbol, side, qty):
        return {
            "ok": False,
            "exchange_order_id": None,
            "error": "Bybit retCode=170127 (order qty below min)",
        }

    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        _fake_close,
    )
    captured_audit = []
    monkeypatch.setattr(
        "src.utils.signal_audit_logger.log_signal",
        lambda payload: captured_audit.append(payload),
    )

    summary = _reconcile_orphan_positions(tmp_db)
    assert summary["sold"] == 0
    assert summary["errors"] == 1
    assert len(captured_audit) == 1
    payload = captured_audit[0]
    assert payload["status"] == "failed"
    assert payload["error"] is not None
    assert "retry next tick" in payload["reason"]
