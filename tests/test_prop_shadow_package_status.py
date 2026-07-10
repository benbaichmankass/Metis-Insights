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
