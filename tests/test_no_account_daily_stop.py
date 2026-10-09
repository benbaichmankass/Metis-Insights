"""No account-wide daily stop (operator decision 2026-10-09).

OPERATOR DECISION 2026-10-09 ~11:40Z (popup relayed by the manager
session), question: "The daily loss cap and the intraday drawdown cap ...
refuse every new entry until UTC midnight. Under 'no halting', what should
they do?" — option chosen, verbatim: "Remove them" ("No account-wide daily
stop at all; only per-trade sizing and the prop-firm floors apply.").
Directive: "There is no halting."

Replaces tests/test_daily_loss_pct_cap.py, which pinned the percentage
daily-loss cap this decision removed. Pins:

1. an account past the old daily-loss cap (absolute OR percentage) still
   approves its next trade, and ``evaluate`` returns no DAILY_LOSS_CAP;
2. an account past the old intraday-drawdown cap still approves, and
   ``evaluate`` returns no INTRADAY_DRAWDOWN;
3. the sizer is NOT clamped or zeroed by the day's realized loss — the
   size after a large loss equals the size on a flat day;
4. the same holds for the prop RiskManager subclass and for every
   ``breach_guards`` value (the key no longer changes RiskManager);
5. daily PnL and intraday drawdown are still COMPUTED (journal rebuild)
   and REPORTED, and nothing is written to breach_accepted.jsonl;
6. the per-trade checks that remain still refuse (dry-run, gross exposure).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.prop_risk import PropRiskManager
from src.units.accounts.risk import RiskManager


def _pkg() -> OrderPackage:
    # entry/sl 100 apart -> risk_distance = 100; cvu = 1.0 (BTCUSDT crypto).
    return OrderPackage(
        strategy="vwap", symbol="BTCUSDT", direction="short",
        entry=80_000.0, sl=80_100.0, tp=79_000.0, confidence=1.0,
        meta={"strategy_name": "vwap"},
    )


def _rm(**risk) -> RiskManager:
    base = {"max_dd_pct": 0.05, "daily_usd": 100, "daily_loss_pct": 0.05,
            "risk_pct": 0.01, "leverage": 3}
    base.update(risk)
    return RiskManager(base, account_id="")  # in-memory


@pytest.mark.parametrize("cfg", [
    {"daily_loss_pct": 0.05},                 # percentage cap profile
    {"daily_loss_pct": 0.0, "daily_usd": 50},  # absolute cap profile (old prop shape)
])
def test_past_old_daily_loss_cap_still_approves(cfg):
    rm = _rm(**cfg)
    rm.current_equity = 274_000.0
    rm.daily_pnl = -1_000_000.0  # far past any old cap
    ok, reason = rm.evaluate(_pkg())
    assert ok is True and reason is None
    assert rm.approve(_pkg()) is True


def test_past_old_drawdown_cap_still_approves():
    rm = _rm()
    rm.update_equity(10_000.0)
    rm.update_equity(5_000.0)  # 50 % intraday drawdown, old cap 5 %
    assert rm.intraday_drawdown() == pytest.approx(0.5)
    ok, reason = rm.evaluate(_pkg())
    assert ok is True and reason is None


def test_next_cycle_proceeds_repeatedly_no_standing_block():
    """No latch: every subsequent evaluation past the old caps proceeds."""
    rm = _rm()
    rm.update_equity(10_000.0)
    rm.update_equity(8_000.0)
    rm.daily_pnl = -5_000.0
    for _ in range(5):
        assert rm.evaluate(_pkg()) == (True, None)


def test_size_not_clamped_by_daily_loss():
    flat = _rm()
    qty_flat = flat.position_size(_pkg(), 274_000.0, market_type="linear",
                                  total_account_usd=274_000.0)
    losing = _rm()
    losing.daily_pnl = -50_000.0  # far past 5 % of 274k
    qty_losing = losing.position_size(_pkg(), 274_000.0, market_type="linear",
                                      total_account_usd=274_000.0)
    assert qty_flat > 0.0
    assert qty_losing == qty_flat


@pytest.mark.parametrize("mode", ["enforce", "report", "typo"])
def test_breach_guards_no_longer_changes_riskmanager(mode, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    rm = _rm(breach_guards=mode, daily_usd=150, max_dd_pct=0.06)
    rm.update_equity(5_000.0)
    rm.update_equity(4_000.0)  # 20 % drawdown
    rm.daily_pnl = -1_000.0
    assert rm.evaluate(_pkg()) == (True, None)
    assert not hasattr(rm, "last_breach_report")
    assert not list(tmp_path.rglob("breach_accepted.jsonl"))


def test_prop_risk_manager_past_old_caps_still_approves():
    prm = PropRiskManager(
        {"risk": {"max_dd_pct": 0.06, "daily_usd": 150, "daily_loss_pct": 0.03,
                  "risk_pct": 0.015},
         "account_state": "funded", "account_size_usd": 5_000},
        account_name=None,
    )
    prm.update_equity(5_000.0)
    prm.update_equity(4_000.0)
    prm.record_trade_result(-1_000.0)
    assert prm.evaluate(_pkg()) == (True, None)
    # Sizes off the nominal equity, not clamped to zero by the day's loss.
    assert prm.position_size(_pkg(), 0.0) > 0.0


def test_daily_figures_still_reported():
    rm = _rm()
    rm.update_equity(10_000.0)
    rm.update_equity(9_000.0)
    rm.record_trade_result(-1_234.5)
    rep = rm.report()
    assert rep["daily_pnl"] == -1_234.5
    assert rep["intraday_drawdown_pct"] == pytest.approx(0.10)
    assert rep["current_equity"] == 9_000.0
    assert rep["daily_high_equity"] == 10_000.0
    for gone in ("halted", "max_daily_loss_usd", "daily_loss_remaining",
                 "max_dd_pct", "daily_loss_pct", "daily_loss_usd_floor"):
        assert gone not in rep


def test_daily_pnl_still_computed_from_journal_and_trade_proceeds(tmp_path, monkeypatch):
    from src.units.db.database import Database

    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data-root"))
    today = str(datetime.now(timezone.utc).date())
    Database(db_path=str(db_path)).insert_trade({
        "timestamp": f"{today}T12:00:00+00:00", "symbol": "BTCUSDT",
        "direction": "long", "entry_price": 50_000.0, "position_size": 0.01,
        "status": "closed", "pnl": -5_000.0, "is_backtest": 0,
        "account_id": "acc_nostop", "created_at": f"{today} 12:00:00",
    })
    rm = RiskManager({"daily_usd": 100.0, "max_dd_pct": 0.05},
                     account_id="acc_nostop")
    assert rm.daily_pnl == pytest.approx(-5_000.0)  # computed
    assert rm.report()["daily_pnl"] == pytest.approx(-5_000.0)  # reported
    assert rm.evaluate(_pkg()) == (True, None)  # not refused


def test_remaining_per_trade_checks_still_refuse():
    # dry-run account: still refuses (account gate, not a daily stop).
    assert RiskManager({}, dry_run=True).evaluate(_pkg()) == (False, "account_mode_dry_run")
    # Gross-exposure ceiling at/over its multiple: still refuses.
    rm = _rm(max_gross_exposure_pct=1.0)
    from src.units.accounts import exposure as _exposure
    rm.observe_exposure = lambda: _exposure.measured(20_000.0, 10_000.0)
    assert rm.evaluate(_pkg()) == (False, "GROSS_EXPOSURE_CAP")
