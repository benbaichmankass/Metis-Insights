"""BL-20260601-001 — orphan-PnL attribution fix (two-pronged).

Covers the close-pnl attribution path end-to-end at the two seams the
fix touches:

  * ``src.units.accounts.clients.account_closed_pnl_for_trade`` — the
    public wrapper that decides whether to widen the lookup (demo) and
    whether to skip the direction/entry filters (reduce leg).
  * ``src.runtime.order_monitor._close_trade_from_order_status`` — the
    reconciler write-back that detects an intent-reduce leg from the
    trade row and forwards ``reduce_leg=True``.

Verified failure pattern (2026-06-08 /performance-review, issue #2974):
5/5 bybit_1 (demo) closed ``htf_pullback`` trades carried ``pnl=NULL``;
live trade #2491 (the only LONG, a reduce leg of pkg-8596863669584ed5)
also NULL. See ``docs/claude/health-review-backlog.json::BL-20260601-001``.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from src.units.accounts.clients import account_closed_pnl_for_trade


def _make_client(records):
    client = MagicMock()
    client.get_closed_pnl.return_value = {"result": {"list": records}}
    return client


def _rec(*, side, qty, entry, exit_, pnl, updated):
    return {
        "side": side,
        "qty": str(qty),
        "avgEntryPrice": str(entry),
        "avgExitPrice": str(exit_),
        "closedPnl": str(pnl),
        "updatedTime": str(updated),
        "createdTime": str(updated),
    }


_OPENED_MS = 1780776000000


def _account(*, demo):
    return {
        "account_id": "bybit_1" if demo else "bybit_2",
        "exchange": "bybit",
        "demo": demo,
        "api_key_env": "BYBIT_KEY",
        "category": "linear",
    }


class TestAccountClosedPnlForTradeDemoFallback:
    """BL-20260608-DEMOPNL / BL-20260620-CLOSEDPNL-LOOKUP-MISMATCH-DEMO:
    the demo flag now SHORT-CIRCUITS the broker-truth lookup entirely
    (returns None before touching the client), so demo realised PnL is
    deferred to the universal local-compute sweep. Bybit's closed-pnl
    endpoint mis-maps records for the demo/testnet account, so the old
    wide fallback booked the wrong record onto separate paper trades.
    Live accounts keep the strict NULL-on-no-match broker-truth contract."""

    def _zeroed_entry_records(self):
        # Demo venue placeholder: avgEntryPrice=0 → every record fails
        # the strict 10-bps entry filter. direction='short' → the close
        # is a Buy.
        return [
            _rec(side="Buy", qty=0.059, entry=0.0, exit_=60500.0,
                 pnl=-3.20, updated=_OPENED_MS + 60_000),
        ]

    def _patches(self, client):
        return (
            patch("src.units.accounts.clients.bybit_client_for",
                  return_value=client),
            patch("src.units.accounts.execute._bybit_category",
                  return_value="linear"),
        )

    def test_demo_short_circuits_lookup_returns_none(self):
        """Demo → None WITHOUT ever calling the Bybit client (the early
        return precedes ``bybit_client_for``), so the unreliable demo
        closed-pnl record can't be booked. The row stays unpriced for the
        local-compute sweep to resolve."""
        client = _make_client(self._zeroed_entry_records())
        p1, p2 = self._patches(client)
        with p1, p2:
            rec = account_closed_pnl_for_trade(
                _account(demo=True),
                symbol="BTCUSDT", direction="short",
                opened_at_ms=_OPENED_MS,
                qty=0.059, entry_price=60568.6,
            )
        assert rec is None
        # The short-circuit must precede the broker call entirely.
        client.get_closed_pnl.assert_not_called()

    def test_live_strict_fail_preserves_null_fallback(self):
        """Same records, LIVE account → strict filter still strands the
        match (returns None). Guards the #1411 / #1419 production
        disambiguation against the demo widening."""
        client = _make_client(self._zeroed_entry_records())
        p1, p2 = self._patches(client)
        with p1, p2:
            rec = account_closed_pnl_for_trade(
                _account(demo=False),
                symbol="BTCUSDT", direction="short",
                opened_at_ms=_OPENED_MS,
                qty=0.059, entry_price=60568.6,
            )
        assert rec is None


class TestAccountClosedPnlForTradeReduceLeg:
    """Prong 2: a reduce leg skips the (flipped) side + (wrong) entry
    filters and matches by position movement."""

    def _reduce_records(self):
        # Buy close of a held short; avgEntryPrice is the short's real
        # entry (60756.7), NOT the recorded primary-leg intent (60774.6).
        return [
            _rec(side="Buy", qty=0.003, entry=60756.7, exit_=60050.0,
                 pnl=2.0988, updated=_OPENED_MS + 1000),
        ]

    def _patches(self, client):
        return (
            patch("src.units.accounts.clients.bybit_client_for",
                  return_value=client),
            patch("src.units.accounts.execute._bybit_category",
                  return_value="linear"),
        )

    def test_reduce_leg_false_misses_the_close(self):
        """Default (reduce_leg=False): direction='long' → Sell lookup +
        entry 60774.6 → no match → NULL (the bug)."""
        client = _make_client(self._reduce_records())
        p1, p2 = self._patches(client)
        with p1, p2:
            rec = account_closed_pnl_for_trade(
                _account(demo=False),
                symbol="BTCUSDT", direction="long",
                opened_at_ms=_OPENED_MS,
                qty=0.003, entry_price=60774.6,
            )
        assert rec is None

    def test_reduce_leg_true_attributes_pnl(self):
        client = _make_client(self._reduce_records())
        p1, p2 = self._patches(client)
        with p1, p2:
            rec = account_closed_pnl_for_trade(
                _account(demo=False),
                symbol="BTCUSDT", direction="long",
                opened_at_ms=_OPENED_MS,
                qty=0.003, entry_price=60774.6,
                reduce_leg=True,
            )
        assert rec is not None
        assert abs(rec["closed_pnl"] - 2.0988) < 1e-4


class _FakeDB:
    """Minimal Database stand-in capturing update_trade calls."""

    def __init__(self):
        self.trade_updates = {}

    def update_trade(self, trade_id, updates):
        self.trade_updates[int(trade_id)] = dict(updates)
        return 1


def _close(row, *, cfg, closed_pnl_rec, capture):
    """Run ``_close_trade_from_order_status`` with the closed-pnl lookup
    stubbed to *closed_pnl_rec* and *capture* the kwargs it was called
    with. The package cascade is no-op'd (covered elsewhere)."""
    from src.runtime import order_monitor as om

    def _fake_lookup(*args, **kwargs):
        capture.update(kwargs)
        return closed_pnl_rec

    db = _FakeDB()
    order_status = {"avg_price": 60800.0, "exec_time": "1780793499383"}
    with patch(
        "src.units.accounts.clients.account_closed_pnl_for_trade",
        side_effect=_fake_lookup,
    ), patch.object(om, "_cascade_close_linked_package", return_value=None):
        om._close_trade_from_order_status(db, row, order_status, cfg=cfg)
    return db


class TestCloseTradeReduceLegDetection:
    """``_close_trade_from_order_status`` must detect an intent-reduce
    leg from the trade row and forward ``reduce_leg=True``."""

    def _base_row(self, **over):
        row = {
            "id": 2491,
            "symbol": "BTCUSDT",
            "direction": "long",
            "position_size": 0.003,
            "entry_price": 60774.6,
            "created_at": "2026-06-07T00:51:39.456241+00:00",
            "setup_type": "htf_pullback_trend_2h",
            "notes": json.dumps({"trade_id": "x", "is_dry": False}),
        }
        row.update(over)
        return row

    def test_reduce_leg_detected_from_setup_type(self):
        cap = {}
        row = self._base_row(setup_type="intent_reduce")
        db = _close(
            row, cfg={"account_id": "bybit_2"},
            closed_pnl_rec={
                "avg_exit_price": 60050.0, "closed_pnl": 2.0988,
                "closed_at": "1780793499383",
            },
            capture=cap,
        )
        assert cap.get("reduce_leg") is True
        # BL-20260711 CONTRACT CHANGE (reverses the #2974 attribution): a reduce
        # leg's pnl is DEFERRED (NULL). On a netting account the qty-matched
        # closed_pnl record is the PARENT position's realized close, so booking
        # it onto this bookkeeping leg fabricates a phantom win/loss (the observed
        # entry==exit +$561/+620/+898 rows). apply_intent_reduce_partial_close
        # leaves reduce-leg pnl NULL by design and the parent's full close carries
        # the realized pnl; the read-path mask (exclude_reduce_leg_predicate)
        # excludes reduce legs regardless, so #2974's attribution populated a
        # field nothing reads. The leg still closes with a recovered exit_price.
        assert db.trade_updates[2491].get("pnl") is None
        assert db.trade_updates[2491]["status"] == "closed"
        _notes = db.trade_updates[2491].get("notes") or ""
        assert "deferred_intent_reduce" in _notes

    def test_reduce_leg_detected_from_notes_flag(self):
        cap = {}
        row = self._base_row(
            notes=json.dumps({"intent_reduce": True, "is_dry": False}),
        )
        _close(
            row, cfg={"account_id": "bybit_2"},
            closed_pnl_rec={
                "avg_exit_price": 60050.0, "closed_pnl": 2.0988,
                "closed_at": "1780793499383",
            },
            capture=cap,
        )
        assert cap.get("reduce_leg") is True

    def test_normal_trade_passes_reduce_leg_false(self):
        cap = {}
        row = self._base_row()  # setup_type=htf_pullback_trend_2h, no flag
        _close(
            row, cfg={"account_id": "bybit_2"},
            closed_pnl_rec=None,
            capture=cap,
        )
        assert cap.get("reduce_leg") is False
