"""Regression: a shadow/dry prop leg must terminalise its order package with a
clean ``status='shadow'`` — NOT leave it 'open' to be mis-swept as 'orphaned'.

Background (system-health-prop-strategies, 2026-07-10): the ``execute_pkg``
breakout branch returns a truthy ``dry-`` trade_id for a shadow/dry prop leg
WITHOUT stamping the order package. The coordinator's BUG-049 no-trade backstop
then treats the leg as placed and never terminalises the package, so the monitor
reconciler mis-stamps it 'orphaned — never executed' at +5min. That made the
shadow prop variants (``trend_donchian_{sol,eth}_prop``, ``execution: shadow``)
show up as alarming "orphaned" rows on ``/api/bot/prop/tickets``. The fix stamps
``status='shadow'`` (a non-'open' status the orphan sweep ignores) in the dry
branch — the mirror of the live branch's ``status='emitted'`` contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


def _breakout_cfg() -> dict:
    return {
        "account_id": "breakout_1",
        "exchange": "breakout",
        "account_class": "prop",
        "risk_pct": 0.015,
    }


def test_shadow_prop_leg_stamps_package_shadow(isolated_env: Path) -> None:
    from src.core.coordinator import OrderPackage
    from src.units.accounts.execute import execute_pkg
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path

    db = Database(db_path=trade_journal_db_path())
    db.insert_order_package({
        "order_package_id": "op-shadow-1",
        "strategy_name": "trend_donchian_eth_prop",
        "symbol": "ETHUSDT",
        "direction": "long",
        "entry": 1773.45,
        "sl": 1742.51,
        "tp": 1949.02,
        "status": "open",
    })

    pkg = OrderPackage(
        strategy="trend_donchian_eth_prop",
        symbol="ETHUSDT",
        direction="long",
        entry=1773.45,
        sl=1742.51,
        tp=1949.02,
        meta={"order_package_id": "op-shadow-1", "timeframe": "1h"},
    )

    # dry_run=True is how the coordinator folds in the per-strategy
    # execution: shadow gate for this leg.
    trade_id = execute_pkg(pkg, _breakout_cfg(), dry_run=True)
    assert trade_id.startswith("dry-")  # no ticket emitted

    rows = db.get_order_packages_by_strategy("trend_donchian_eth_prop")
    assert len(rows) == 1
    # The package is terminalised 'shadow' — NOT left 'open' (which the +5min
    # orphan sweep would flip to 'orphaned'), and NOT 'emitted' (a real ticket).
    assert rows[0]["status"] == "shadow"
    assert rows[0]["close_reason"] == "prop_shadow_no_emit"


def test_shadow_leg_without_pkg_id_is_noop(isolated_env: Path) -> None:
    """No order_package_id in meta → the stamping is skipped cleanly (the leg
    still returns a dry trade_id; the best-effort stamp never raises)."""
    from src.core.coordinator import OrderPackage
    from src.units.accounts.execute import execute_pkg

    pkg = OrderPackage(
        strategy="trend_donchian_sol_prop",
        symbol="SOLUSDT",
        direction="long",
        entry=79.31,
        sl=77.79,
        tp=87.16,
        meta={"timeframe": "1h"},  # no order_package_id
    )
    trade_id = execute_pkg(pkg, _breakout_cfg(), dry_run=True)
    assert trade_id.startswith("dry-")


# ── one package, two accounts (manager review of #14672, TRADEIFY-WIRE) ───
# The order_package_id is shared by every account a signal fans out to. A DRY
# prop account (tradeify_1 at mode: dry_run) dispatched AFTER a live account
# must never overwrite that account's outcome on the same row.


def _seed(db, pkg_id: str, **over) -> None:
    row = {"order_package_id": pkg_id, "strategy_name": "trend_donchian_sol_prop",
           "symbol": "SOLUSDT", "direction": "long", "entry": 150.0, "sl": 145.0,
           "tp": 162.0, "status": "open"}
    row.update(over)
    db.insert_order_package(row)


def _pkg(pkg_id: str):
    from src.core.coordinator import OrderPackage
    return OrderPackage(strategy="trend_donchian_sol_prop", symbol="SOLUSDT", direction="long",
                        entry=150.0, sl=145.0, tp=162.0,
                        meta={"order_package_id": pkg_id, "timeframe": "1h"})


def _row(db, pkg_id: str) -> dict:
    return next(r for r in db.get_order_packages_by_strategy("trend_donchian_sol_prop")
                if r["order_package_id"] == pkg_id)


def test_dry_account_after_a_live_emission_does_not_overwrite_it(isolated_env: Path) -> None:
    from src.units.accounts.execute import execute_pkg
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path

    db = Database(db_path=trade_journal_db_path())
    _seed(db, "op-shared-1")
    # breakout_1 (live) emitted on this package first …
    db.update_order_package("op-shared-1", {"status": "emitted", "close_reason": "prop_ticket_emitted"})
    # … then the dry prop account runs on the SAME package.
    dry_cfg = {**_breakout_cfg(), "account_id": "tradeify_1"}
    assert execute_pkg(_pkg("op-shared-1"), dry_cfg, dry_run=True).startswith("dry-")
    r = _row(db, "op-shared-1")
    assert (r["status"], r["close_reason"]) == ("emitted", "prop_ticket_emitted")


def test_dry_account_after_a_live_fill_does_not_overwrite_it(isolated_env: Path) -> None:
    from src.units.accounts.execute import execute_pkg
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path

    db = Database(db_path=trade_journal_db_path())
    _seed(db, "op-shared-2")
    # a live broker account (e.g. bybit_1 on ict_scalp_xrp_15m) filled and linked
    assert db.update_order_package_linked_trade_if_unset("op-shared-2", 4242) == 1
    dry_cfg = {**_breakout_cfg(), "account_id": "tradeify_1"}
    execute_pkg(_pkg("op-shared-2"), dry_cfg, dry_run=True)
    r = _row(db, "op-shared-2")
    assert r["status"] == "open" and str(r["linked_trade_id"]) == "4242"


def test_helper_writes_only_an_untouched_open_package(isolated_env: Path) -> None:
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path

    db = Database(db_path=trade_journal_db_path())
    _seed(db, "op-a")
    _seed(db, "op-b", status="emitted")
    assert db.mark_order_package_shadow_if_untouched("op-a", "prop_shadow_no_emit") == 1
    assert db.mark_order_package_shadow_if_untouched("op-b", "prop_shadow_no_emit") == 0
    assert db.mark_order_package_shadow_if_untouched("op-missing", "prop_shadow_no_emit") == 0


# ── live branch: refused tickets and shared packages (PI-20260930-BHYHMK2H-0001) ──


def _live(monkeypatch, db, pkg_id, ticket_status):
    """Run the LIVE prop branch with the emitter stubbed to journal one ticket
    in ``ticket_status`` (``None`` = no ticket row)."""
    from src.prop import breakout_executor, prop_journal
    from src.units.accounts.execute import execute_pkg

    def _emit(order, cfg, **kw):
        tid = f"prop-manual-{pkg_id}"
        if ticket_status:
            prop_journal.record_ticket({"ticket_id": tid, "account_id": cfg["account_id"],
                                        "symbol": "SOLUSDT", "direction": "long",
                                        "status": ticket_status})
        return tid

    monkeypatch.setattr(breakout_executor, "emit_prop_ticket", _emit)
    return execute_pkg(_pkg(pkg_id), _breakout_cfg(), dry_run=False)


def test_live_emitted_ticket_stamps_emitted(isolated_env: Path, monkeypatch) -> None:
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path

    db = Database(db_path=trade_journal_db_path())
    _seed(db, "op-live-1")
    _live(monkeypatch, db, "op-live-1", "emitted")
    r = _row(db, "op-live-1")
    assert (r["status"], r["close_reason"]) == ("emitted", "prop_ticket_emitted")


def test_live_refused_ticket_stamps_rejected_not_emitted(isolated_env: Path, monkeypatch) -> None:
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path

    db = Database(db_path=trade_journal_db_path())
    _seed(db, "op-live-2")
    _live(monkeypatch, db, "op-live-2", "skipped")   # e.g. a leverage-cap refusal
    r = _row(db, "op-live-2")
    assert (r["status"], r["close_reason"]) == ("rejected", "prop_ticket_skipped")


def test_live_prop_leg_never_overwrites_another_accounts_fill(isolated_env: Path, monkeypatch) -> None:
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path

    db = Database(db_path=trade_journal_db_path())
    _seed(db, "op-live-3")
    assert db.update_order_package_linked_trade_if_unset("op-live-3", 777) == 1
    _live(monkeypatch, db, "op-live-3", "emitted")
    r = _row(db, "op-live-3")
    assert r["status"] == "open" and str(r["linked_trade_id"]) == "777"
