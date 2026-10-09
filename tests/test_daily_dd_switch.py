"""The account-level daily-drawdown switch — rule 7's ONE sanctioned off.

Operator directive 2026-10-09 (docs/CLAUDE-RULES-CANONICAL.md § Prime
Directive rule 7): default DISARMED; when armed and the account's equity
drawdown from the day start reaches the limit, NEW entries are refused
(``DAILY_DD_SWITCH``) until the daily reset. Open positions keep SL/TP and
reduce-only orders pass. One alert on trip, one on reset; one red flag after
3 consecutive unreadable checks. Non-prop limit 3% of day-start equity; prop
limit 0.8 x the firm's daily limit at the firm's reset time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from src.core.coordinator import OrderPackage
from src.units.accounts import daily_dd_switch as dd
from src.units.accounts.prop_risk import PropRiskManager
from src.units.accounts.risk import RiskManager

_REPO = Path(__file__).resolve().parents[1]


def _ts(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=timezone.utc)


def _switch(tmp_path, *, armed=True, prop_terms=None, block=None):
    alerts: list = []
    blk = {"armed": armed} if block is None else block
    sw = dd.DailyDDSwitch(
        "acct", dd.parse_config(blk, prop_terms=prop_terms),
        state_path=tmp_path / "s.json",
        alert=lambda kind, acct, row, cfg: alerts.append(kind),
    )
    return sw, alerts


def _pkg() -> OrderPackage:
    return OrderPackage(strategy="vwap", symbol="BTCUSDT", direction="long",
                        entry=80_000.0, sl=79_900.0, tp=81_000.0, confidence=1.0,
                        meta={"strategy_name": "vwap"})


# -- config ----------------------------------------------------------------

@pytest.mark.parametrize("block", [None, {}, {"armed": False}, {"armed": "true"}, {"armed": 1}])
def test_anything_but_literal_true_is_disarmed(block):
    assert dd.parse_config(block).armed is False


def test_non_prop_default_limit_is_3pct():
    cfg = dd.parse_config({"armed": True})
    assert cfg.limit_pct == 0.03 and cfg.basis == dd.BASIS_PCT
    assert cfg.limit_usd(10_000.0) == pytest.approx(300.0)
    assert cfg.reset_utc == "00:00"


def test_prop_limit_is_80pct_of_firm_limit_on_day_start_basis():
    terms = {"daily_loss_pct": 0.04, "daily_loss_reset_utc": "00:30",
             "daily_loss_amount_basis": "day_start_balance", "account_size_usd": 5000}
    cfg = dd.parse_config({"armed": True}, prop_terms=terms)
    assert cfg.basis == dd.BASIS_PROP and cfg.buffer == 0.20
    assert cfg.limit_usd(5_000.0) == pytest.approx(0.8 * 0.04 * 5_000.0)   # $160
    assert cfg.limit_usd(4_500.0) == pytest.approx(0.8 * 0.04 * 4_500.0)


def test_prop_limit_on_account_size_basis_ignores_day_start():
    terms = {"daily_loss_pct": 0.03, "daily_loss_reset_utc": "22:00",
             "daily_loss_amount_basis": "account_size", "account_size_usd": 10_000}
    cfg = dd.parse_config({"armed": True}, prop_terms=terms)
    assert cfg.limit_usd(9_000.0) == pytest.approx(240.0)


def test_unreadable_prop_ruleset_has_no_limit_and_says_so():
    cfg = dd.parse_config({"armed": True}, prop_terms={})
    assert cfg.limit_usd(5_000.0) is None
    assert cfg.config_error


def test_real_prop_accounts_read_their_firm_terms():
    accounts = yaml.safe_load((_REPO / "config/accounts.yaml").read_text())["accounts"]
    want = {"breakout_2": (0.03, "00:30"), "tradeify_1": (0.03, "22:00"),
            "velotrade_1": (0.04, "00:30")}
    for name, (pct, reset) in want.items():
        sw = dd.switch_for_account(name, accounts[name])
        assert sw.cfg.basis == dd.BASIS_PROP, name
        assert sw.cfg.firm_daily_loss_pct == pytest.approx(pct), name
        assert sw.cfg.reset_utc == reset, name
        assert sw.cfg.config_error is None, name
    assert dd.switch_for_account("tradeify_1", accounts["tradeify_1"]).cfg.limit_usd(None) \
        == pytest.approx(240.0)


# -- the check -------------------------------------------------------------

def test_disarmed_is_a_no_op_past_the_limit(tmp_path):
    sw, alerts = _switch(tmp_path, armed=False)
    sw.observe(10_000.0, _ts(9, 1))
    row = sw.observe(5_000.0, _ts(9, 2))      # 50% down
    assert sw.blocks_new_entries(row, _ts(9, 2)) is False
    assert row["tripped"] is False and alerts == []
    # Still observed + reported, so the operator can see what arming would do.
    assert row["loss_usd"] == pytest.approx(5_000.0)


def test_trips_at_limit_with_one_alert_and_latches(tmp_path):
    sw, alerts = _switch(tmp_path)
    sw.observe(10_000.0, _ts(9, 1))
    assert not sw.blocks_new_entries(sw.observe(9_750.0, _ts(9, 2)), _ts(9, 2))   # 2.5%
    row = sw.observe(9_700.0, _ts(9, 3))                                         # 3.0%
    assert sw.blocks_new_entries(row, _ts(9, 3)) and alerts == ["trip"]
    # Recovery inside the day does NOT clear it; no duplicate alert.
    row = sw.observe(10_500.0, _ts(9, 4))
    assert sw.blocks_new_entries(row, _ts(9, 4)) and alerts == ["trip"]


def test_resets_at_utc_midnight_with_one_alert(tmp_path):
    sw, alerts = _switch(tmp_path)
    sw.observe(10_000.0, _ts(9, 1))
    sw.observe(9_000.0, _ts(9, 2))
    assert alerts == ["trip"]
    assert sw.blocks_new_entries(None, _ts(9, 23, 59))
    assert not sw.blocks_new_entries(None, _ts(10, 0, 1))   # new day, before any read
    row = sw.observe(9_000.0, _ts(10, 0, 5))
    assert alerts == ["trip", "reset"]
    assert row["tripped"] is False and not sw.blocks_new_entries(row, _ts(10, 0, 5))
    # Day-start equity re-anchors to the last reading within 2h of the boundary.
    assert row["day_start_equity"] == pytest.approx(9_000.0)


def test_prop_resets_at_the_firms_reset_time(tmp_path):
    terms = {"daily_loss_pct": 0.03, "daily_loss_reset_utc": "22:00",
             "daily_loss_amount_basis": "account_size", "account_size_usd": 10_000}
    sw, alerts = _switch(tmp_path, prop_terms=terms)
    sw.observe(10_000.0, _ts(9, 22, 30))        # firm day opened 22:00 on the 9th
    row = sw.observe(9_760.0, _ts(9, 23))       # $240 = 0.8 x 3% x 10k
    assert sw.blocks_new_entries(row, _ts(9, 23)) and alerts == ["trip"]
    assert sw.blocks_new_entries(None, _ts(10, 0, 30))  # past UTC midnight: same firm day
    assert sw.blocks_new_entries(None, _ts(10, 21, 59))
    row = sw.observe(9_760.0, _ts(10, 22, 1))   # firm reset
    assert alerts == ["trip", "reset"] and not sw.blocks_new_entries(row, _ts(10, 22, 1))


def test_unreadable_never_trips_and_red_flags_once_after_3(tmp_path):
    sw, alerts = _switch(tmp_path)
    sw.observe(10_000.0, _ts(9, 1))
    for i in range(2):
        sw.observe(None, _ts(9, 2 + i))
    assert alerts == []
    row = sw.observe(None, _ts(9, 4))
    assert alerts == ["unreadable"] and row["read_state"] == dd.READ_UNREADABLE
    sw.observe(0.0, _ts(9, 5))
    sw.observe("garbage", _ts(9, 6))
    assert alerts == ["unreadable"]                        # one flag, checks keep running
    assert not sw.blocks_new_entries(None, _ts(9, 6))      # could-not-look never trips
    row = sw.observe(10_000.0, _ts(9, 7))                  # recovery re-arms the flag
    assert row["unreadable_streak"] == 0 and row["red_flagged"] is False


def test_unreadable_does_not_clear_a_trip(tmp_path):
    sw, _ = _switch(tmp_path)
    sw.observe(10_000.0, _ts(9, 1))
    sw.observe(9_000.0, _ts(9, 2))
    sw.observe(None, _ts(9, 3))
    assert sw.blocks_new_entries(None, _ts(9, 3))


def test_disarmed_unreadable_sends_no_flag(tmp_path):
    sw, alerts = _switch(tmp_path, armed=False)
    for i in range(5):
        sw.observe(None, _ts(9, 1 + i))
    assert alerts == []


def test_status_shape(tmp_path):
    sw, _ = _switch(tmp_path)
    assert sw.status()["read_state"] == dd.READ_NOT_OBSERVED
    sw.observe(10_000.0, _ts(9, 1))
    st = sw.status()
    for key in ("armed", "limit_desc", "limit_usd", "day_start_equity", "tripped", "reset_utc"):
        assert key in st
    assert st["armed"] is True and st["limit_usd"] == pytest.approx(300.0)


# -- RiskManager integration -----------------------------------------------

def _rm(armed=True):
    return RiskManager({"daily_dd_switch": {"armed": armed}}, account_id="rm_dd")


def test_risk_manager_refuses_new_entry_but_passes_reduce_only():
    rm = _rm()
    rm.note_live_equity(10_000.0)
    assert rm.evaluate(_pkg()) == (True, None)
    rm.note_live_equity(9_600.0)
    assert rm.evaluate(_pkg()) == (False, "DAILY_DD_SWITCH")
    assert rm.evaluate(_pkg(), opening=False) == (True, None)
    assert rm.report()["halted"] is True
    assert rm.report()["daily_dd_switch"]["tripped"] is True


def test_risk_manager_disarmed_never_refuses():
    rm = _rm(armed=False)
    rm.note_live_equity(10_000.0)
    rm.evaluate(_pkg())
    rm.note_live_equity(1_000.0)
    assert rm.evaluate(_pkg()) == (True, None)
    assert rm.report()["halted"] is False


def test_risk_manager_switch_bug_never_refuses(monkeypatch):
    rm = _rm()

    def boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(rm.dd_switch, "observe", boom)
    assert rm.evaluate(_pkg()) == (True, None)


def test_prop_risk_manager_uses_firm_limit():
    accounts = yaml.safe_load((_REPO / "config/accounts.yaml").read_text())["accounts"]
    cfg = dict(accounts["tradeify_1"])
    cfg["risk"] = dict(cfg.get("risk") or {}, daily_dd_switch={"armed": True})
    rm = PropRiskManager(cfg, account_name="tradeify_1_test")
    assert rm.dd_switch.cfg.basis == dd.BASIS_PROP
    assert rm.dd_switch.cfg.limit_usd(None) == pytest.approx(240.0)


# -- the real config -------------------------------------------------------

def _live_accounts():
    accounts = yaml.safe_load((_REPO / "config/accounts.yaml").read_text())["accounts"]
    return {k: v for k, v in accounts.items() if isinstance(v, dict) and v.get("retired") is not True}


def test_every_non_retired_account_declares_the_switch_explicitly():
    """Arming must be a one-field flip, so every account carries the block."""
    for name, cfg in _live_accounts().items():
        blk = (cfg.get("risk") or {}).get("daily_dd_switch")
        assert isinstance(blk, dict), f"{name}: no risk.daily_dd_switch block"
        assert isinstance(blk.get("armed"), bool), f"{name}: armed must be a literal bool"


def test_retired_breakout_1_carries_no_switch():
    accounts = yaml.safe_load((_REPO / "config/accounts.yaml").read_text())["accounts"]
    assert "daily_dd_switch" not in (accounts["breakout_1"].get("risk") or {})


def test_bot_config_surfaces_the_switch():
    from src.web.api.routers.bot_config import build_config
    out = {a["id"]: a for a in build_config()["accounts"]}
    assert out["breakout_1"]["daily_dd_switch"] is None
    for name in _live_accounts():
        st = out[name]["daily_dd_switch"]
        assert isinstance(st, dict) and "armed" in st and "tripped" in st, name
        assert "day_start_equity" in st and "limit_desc" in st, name
