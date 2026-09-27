"""FIX-CA-07 (CA-A02) — a journal read failure must not read as "flat".

Before the fix, ``current_net_position_qty`` returned ``0.0`` and
``has_open_trade_for_strategy`` returned ``False`` when the SELECT raised
(e.g. ``sqlite3.OperationalError('database is locked')``). The intent-mode
branch of ``Coordinator.multi_account_execute`` then dispatched a fresh
full-size ``open`` on an account that may already hold the symbol.

After the fix both helpers return ``None`` ("we could not look"), distinct
from ``0.0`` / ``False`` ("we looked and it is flat"), and the coordinator
refuses that account for that package with ``net_position_unreadable`` — a
per-trade refusal with a logged cause; the account stays live.
"""
from __future__ import annotations

import sqlite3

import src.runtime.positions as positions_mod
from src.runtime.positions import (
    current_net_position_qty,
    has_open_trade_for_strategy,
)
from tests import test_intent_delta_dispatch as _dispatch
from tests.test_intent_delta_dispatch import (
    _init_trade_journal,
    _insert_trade,
    _intent_pkg,
    _patch_dispatch_deps,
)

# Fixtures reused from the intent-dispatch suite (bound, not imported, so the
# test parameters that request them are not import redefinitions).
accounts_yaml = _dispatch.accounts_yaml
coord = _dispatch.coord
trade_db = _dispatch.trade_db


def _locked_connect(*_a, **_k):
    raise sqlite3.OperationalError("database is locked")


class TestHelpersDistinguishUnreadableFromFlat:
    def test_net_qty_is_none_when_select_raises(self, tmp_path, monkeypatch):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        monkeypatch.setattr(positions_mod.sqlite3, "connect", _locked_connect)
        assert current_net_position_qty("bybit_2", "BTCUSDT", db_path=path) is None

    def test_net_qty_flat_is_still_zero(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        assert current_net_position_qty("bybit_2", "BTCUSDT", db_path=path) == 0.0

    def test_open_trade_is_none_when_select_raises(self, tmp_path, monkeypatch):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        monkeypatch.setattr(positions_mod.sqlite3, "connect", _locked_connect)
        assert has_open_trade_for_strategy(
            "bybit_2", "BTCUSDT", "htf_pullback", db_path=path,
        ) is None


class TestCoordinatorRefusesOnUnreadableNetPosition:
    def test_intent_mode_refuses_instead_of_opening(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        captured: list = []
        _patch_dispatch_deps(monkeypatch, captured)
        # The account really holds 0.2 BTC long — the read just fails.
        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.2,
        )
        monkeypatch.setattr(positions_mod.sqlite3, "connect", _locked_connect)

        pkg = _intent_pkg(direction="long", aggregated_target_qty=0.0)
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=lambda account: 10_000.0,
        )

        assert captured == [], "execute_pkg must not be called on an unreadable net position"
        assert len(results) == 1
        assert results[0]["error"] == "net_position_unreadable"
        assert results[0]["trade_id"] is None
