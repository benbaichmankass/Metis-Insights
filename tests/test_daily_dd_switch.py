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

import json
from datetime import datetime, timedelta, timezone
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


# -- the per-tick observe (PI-20261009-VQIPJE2H-0001) ----------------------
# Before it, observe() ran only inside RiskManager.evaluate(), so a quiet
# account was never observed: 13 of 13 armed accounts read not_observed 1h35m
# after arming, and day-start equity would have been the equity at the day's
# FIRST signal, hiding any loss accrued before it.

@pytest.fixture
def tick_env(tmp_path, monkeypatch):
    """A tmp runtime_logs/ (real state-file path, no override), a captured
    alert sink, and helpers to write the balance snapshot + accounts.yaml."""
    import src.utils.paths as paths
    logs = tmp_path / "runtime_logs"
    logs.mkdir()
    monkeypatch.setattr(paths, "runtime_logs_dir", lambda: logs)
    monkeypatch.setattr(dd, "_PATH_OVERRIDE", None)
    alerts: list = []
    monkeypatch.setattr(dd, "_default_alert",
                        lambda kind, acct, row, cfg: alerts.append((kind, acct)))

    accounts = {
        "acct_tick": {"exchange": "bybit", "risk": {"daily_dd_switch": {"armed": True, "limit_pct": 0.03}}},
        "acct_off": {"exchange": "bybit", "risk": {"daily_dd_switch": {"armed": False}}},
        "acct_gone": {"exchange": "bybit", "retired": True,
                      "risk": {"daily_dd_switch": {"armed": True}}},
    }
    acc_path = tmp_path / "accounts.yaml"

    def write_accounts(extra=None):
        acc_path.write_text(yaml.safe_dump({"accounts": {**accounts, **(extra or {})}}))

    def snap(balances: dict, ts: datetime):
        (logs / "balance_snapshots.json").write_text(json.dumps(
            {k: {"balance": v, "ts": ts.isoformat()} for k, v in balances.items()}))

    def tick(now=None):
        return dd.observe_armed_accounts(acc_path, now=now)

    def state():
        return json.loads((logs / "daily_dd_switch_state.json").read_text())

    write_accounts()
    return {"alerts": alerts, "snap": snap, "tick": tick, "state": state,
            "write_accounts": write_accounts, "logs": logs}


def test_tick_without_a_signal_writes_day_start_equity(tick_env):
    now = _ts(9, 21, 0)
    tick_env["snap"]({"acct_tick": 10_000.0, "acct_off": 5_000.0}, _ts(9, 20, 30))
    assert tick_env["tick"](now) == {"acct_tick": "ok"}          # disarmed + retired skipped
    row = tick_env["state"]()["acct_tick"]
    assert row["day_start_equity"] == 10_000.0
    assert row["read_state"] == "ok" and row["loss_usd"] == 0.0 and row["limit_usd"] == 300.0
    assert row["last_equity_ts"] == _ts(9, 20, 30).isoformat()  # the READING's time, not the tick's
    assert "acct_off" not in tick_env["state"]() and not tick_env["alerts"]


def test_loss_between_signals_trips_on_the_tick_once_and_evaluate_refuses(tick_env):
    now = datetime.now(timezone.utc)
    tick_env["snap"]({"acct_tick": 10_000.0}, now - timedelta(minutes=2))
    tick_env["tick"]()
    tick_env["snap"]({"acct_tick": 9_650.0}, now - timedelta(minutes=1))   # -3.5%, no signal
    tick_env["tick"]()
    tick_env["tick"]()
    assert tick_env["alerts"] == [("trip", "acct_tick")]                  # ONE alert, from the tick
    assert tick_env["state"]()["acct_tick"]["tripped"] is True
    rm = RiskManager({"daily_dd_switch": {"armed": True}}, account_id="acct_tick")
    assert rm.evaluate(_pkg()) == (False, "DAILY_DD_SWITCH")
    assert rm.evaluate(_pkg(), opening=False) == (True, None)
    assert tick_env["alerts"] == [("trip", "acct_tick")]


def test_reset_alert_fires_at_the_boundary_with_no_signal(tick_env):
    tick_env["snap"]({"acct_tick": 10_000.0}, _ts(9, 10, 0))
    tick_env["tick"](_ts(9, 10, 5))
    tick_env["snap"]({"acct_tick": 9_600.0}, _ts(9, 23, 30))
    tick_env["tick"](_ts(9, 23, 31))
    assert tick_env["alerts"] == [("trip", "acct_tick")]
    tick_env["tick"](_ts(10, 0, 1))                      # first tick of the new day, same snapshot
    tick_env["tick"](_ts(10, 0, 2))
    assert tick_env["alerts"] == [("trip", "acct_tick"), ("reset", "acct_tick")]
    row = tick_env["state"]()["acct_tick"]
    assert row["day"] == "2026-10-10" and row["tripped"] is False
    assert row["day_start_equity"] == 9_600.0            # carried: read 30 min before the boundary


@pytest.mark.parametrize("case", ["absent", "stale"])
def test_unreadable_equity_on_the_tick_never_trips(tick_env, case):
    tick_env["snap"]({"acct_tick": 10_000.0}, _ts(9, 10, 0))
    tick_env["tick"](_ts(9, 10, 1))
    if case == "absent":
        (tick_env["logs"] / "balance_snapshots.json").unlink()
    else:   # a 1-dollar balance read 3h ago: older than the 2h freshness bound
        tick_env["snap"]({"acct_tick": 1.0}, _ts(9, 11, 0))
    for m in range(4):
        assert tick_env["tick"](_ts(9, 14, m)) == {"acct_tick": "could_not_look"}
    row = tick_env["state"]()["acct_tick"]
    assert row["tripped"] is False and row["day_start_equity"] == 10_000.0
    assert tick_env["alerts"] == [("unreadable", "acct_tick")]      # ONE red flag, never a trip


def test_an_older_snapshot_never_regresses_a_live_read(tick_env):
    now = datetime.now(timezone.utc)
    rm = RiskManager({"daily_dd_switch": {"armed": True}}, account_id="acct_tick")
    rm.note_live_equity(10_000.0)
    rm.evaluate(_pkg())
    tick_env["snap"]({"acct_tick": 9_000.0}, now - timedelta(minutes=30))   # read before the live one
    tick_env["tick"]()
    row = tick_env["state"]()["acct_tick"]
    assert row["last_equity"] == 10_000.0 and row["tripped"] is False and not tick_env["alerts"]


def test_yesterdays_reading_is_not_todays_equity(tmp_path):
    sw, alerts = _switch(tmp_path)
    row = sw.observe(10_000.0, _ts(9, 12, 0), reading_ts=_ts(8, 18, 0))
    assert row["read_state"] == dd.READ_UNREADABLE and row.get("day_start_equity") is None


def test_prop_accounts_are_observed_on_the_tick_from_their_status_row(tick_env, monkeypatch):
    accounts = yaml.safe_load((_REPO / "config/accounts.yaml").read_text())["accounts"]
    cfg = dict(accounts["tradeify_1"])
    cfg["risk"] = dict(cfg.get("risk") or {}, daily_dd_switch={"armed": True})
    tick_env["write_accounts"]({"tradeify_1": cfg})
    import src.prop.prop_balance as pb
    monkeypatch.setattr(pb, "prop_sizing_balance",
                        lambda aid: ("ok", 5_000.0, {"age_hours": 0.5}) if aid == "tradeify_1"
                        else ("absent", None, {}))
    tick_env["snap"]({"acct_tick": 10_000.0}, _ts(9, 12, 0))
    out = tick_env["tick"](_ts(9, 12, 1))
    assert out["tradeify_1"] == "ok"
    row = tick_env["state"]()["tradeify_1"]
    assert row["day_start_equity"] == 5_000.0 and row["limit_usd"] == pytest.approx(240.0)
    # A stale status row is "could not look", never a figure.
    monkeypatch.setattr(pb, "prop_sizing_balance", lambda aid: ("stale", None, {}))
    assert tick_env["tick"](_ts(9, 13, 0))["tradeify_1"] == "could_not_look"


def test_main_loop_calls_the_tick_observe():
    src = (_REPO / "src/main.py").read_text()
    assert "observe_armed_accounts()" in src


# ── PI-20261009-CJWUMAVA-0001 / PI-20261010-CJWUMAVA-0001 ──────────────────
# ib_live (armed, mode dry_run) read could_not_look with a climbing streak:
# the read path never dials a dry gateway, so it can never be read. breakout_2
# (phone-flow prop) read could_not_look all of 2026-10-10: its only equity
# source is the phone, which reports while a ticket waits, and its 15:01Z
# snapshot predated the 00:30 reset's carry window.

def test_dry_account_is_not_read_by_design_never_ok_never_flagged(tick_env):
    tick_env["write_accounts"]({"acct_dry": {"exchange": "interactive_brokers", "mode": "dry_run",
                                             "risk": {"daily_dd_switch": {"armed": True}}}})
    for m in range(5):
        assert tick_env["tick"](_ts(9, 14, m))["acct_dry"] == dd.READ_DRY_NOT_READ
    row = tick_env["state"]()["acct_dry"]
    assert row["unreadable_streak"] == 0 and row["tripped"] is False
    assert row.get("day_start_equity") is None
    assert not [a for a in tick_env["alerts"] if a[1] == "acct_dry"]


def test_quiescent_pre_day_reading_is_carried_under_its_own_label(tmp_path):
    sw, alerts = _switch(tmp_path)
    row = sw.observe(5_000.0, _ts(10, 5, 0), reading_ts=_ts(9, 15, 0), still_valid=lambda s: True)
    assert row["read_state"] == dd.READ_CARRIED != dd.READ_OK
    assert row["carried_from"] == _ts(9, 15, 0).isoformat()
    assert row["day_start_equity"] == 5_000.0 and row["last_equity_ts"] == _ts(9, 15, 0).isoformat()
    assert sw.status(row)["read_state"] == "carried_quiescent"
    # The first equity-moving event since the snapshot drops it to could-not-look.
    row = sw.observe(5_000.0, _ts(10, 6, 0), reading_ts=_ts(9, 15, 0), still_valid=lambda s: False)
    assert row["read_state"] == dd.READ_UNREADABLE
    # A failing check is "could not look", never carried.
    def boom(_s):
        raise RuntimeError("db gone")
    row = sw.observe(5_000.0, _ts(10, 7, 0), reading_ts=_ts(9, 15, 0), still_valid=boom)
    assert row["read_state"] == dd.READ_UNREADABLE and not alerts
    # A fresh reading is ok again and clears carried_from.
    row = sw.observe(4_990.0, _ts(10, 8, 0), reading_ts=_ts(10, 7, 59), still_valid=lambda s: False)
    assert row["read_state"] == dd.READ_OK and row["carried_from"] is None


def test_carried_reading_lets_a_later_fresh_loss_trip(tmp_path):
    sw, alerts = _switch(tmp_path)
    sw.observe(10_000.0, _ts(10, 1, 0), reading_ts=_ts(9, 15, 0), still_valid=lambda s: True)
    row = sw.observe(9_650.0, _ts(10, 9, 0), reading_ts=_ts(10, 9, 0))
    assert row["tripped"] is True and alerts == ["trip"]


def _prop_rows(monkeypatch, *, fills=(), tickets=()):
    import src.prop.prop_journal as pj
    import src.prop.prop_monitor_pulse as pmp
    monkeypatch.setattr(pj, "list_fills", lambda **k: list(fills))
    monkeypatch.setattr(pj, "list_tickets", lambda **k: list(tickets))
    monkeypatch.setattr(pmp, "find_open_prop_positions",
                        lambda **k: [f for f in fills if f.get("status") in ("open", "filled")])


def test_prop_quiescent_since(monkeypatch):
    since, now = _ts(9, 15, 0), _ts(10, 5, 0)
    old = {"status": "closed", "reported_at": _ts(8, 1).isoformat(), "closed_at": _ts(8, 1).isoformat(),
           "opened_at": _ts(8, 0).isoformat(), "created_at": _ts(8, 1).isoformat()}
    dry_test = {"status": "dry_filled", "created_at": _ts(7, 18).isoformat()}
    _prop_rows(monkeypatch, fills=[old], tickets=[dry_test])
    assert dd.prop_quiescent_since("breakout_2", since, now=now) is True
    # A fill after the snapshot realized P&L: not quiescent (the cushion's QUIESCENT ignores it).
    _prop_rows(monkeypatch, fills=[dict(old, reported_at=_ts(9, 20).isoformat())])
    assert dd.prop_quiescent_since("breakout_2", since, now=now) is False
    # An open position.
    _prop_rows(monkeypatch, fills=[dict(old, status="open", closed_at=None)])
    assert dd.prop_quiescent_since("breakout_2", since, now=now) is False
    # A live ticket created after the snapshot.
    _prop_rows(monkeypatch, tickets=[{"status": "emitted", "created_at": _ts(10, 4).isoformat(),
                                      "valid_until": _ts(10, 6).isoformat()}])
    assert dd.prop_quiescent_since("breakout_2", since, now=now) is False

    import src.prop.prop_journal as pj
    def boom(**k):
        raise RuntimeError("locked")
    monkeypatch.setattr(pj, "list_fills", boom)
    with pytest.raises(RuntimeError):   # the switch's _still_valid turns this into could-not-look
        dd.prop_quiescent_since("breakout_2", since, now=now)


def test_tick_carries_a_quiet_prop_account_across_its_reset(tick_env, monkeypatch):
    accounts = yaml.safe_load((_REPO / "config/accounts.yaml").read_text())["accounts"]
    cfg = dict(accounts["breakout_2"])
    tick_env["write_accounts"]({"breakout_2": cfg})
    import src.prop.prop_balance as pb
    import src.prop.prop_journal as pj
    monkeypatch.setattr(pb, "prop_sizing_balance",
                        lambda aid: ("ok", 5_000.0, {"age_hours": 14.9}) if aid == "breakout_2"
                        else ("absent", None, {}))
    monkeypatch.setattr(pj, "latest_account_status",
                        lambda aid: {"reported_at": _ts(9, 15, 1).isoformat(), "equity": 5_000.0})
    _prop_rows(monkeypatch)
    assert tick_env["tick"](_ts(10, 5, 54))["breakout_2"] == dd.READ_CARRIED
    row = tick_env["state"]()["breakout_2"]
    assert row["day_start_equity"] == 5_000.0 and row["limit_usd"] == pytest.approx(120.0)
    # A ticket placed after the snapshot: could-not-look again, counting toward the flag.
    _prop_rows(monkeypatch, tickets=[{"status": "placed", "created_at": _ts(10, 6).isoformat()}])
    for m in range(3):
        assert tick_env["tick"](_ts(10, 7, m))["breakout_2"] == dd.READ_UNREADABLE
    assert ("unreadable", "breakout_2") in tick_env["alerts"]   # the streak now advances (no ts jitter)


def test_prop_risk_manager_carries_quiescent_reading(monkeypatch, tmp_path):
    monkeypatch.setattr(dd, "prop_quiescent_since", lambda name, since, now=None: True)
    monkeypatch.setattr(dd, "prop_equity_reading",
                        lambda aid, now=None: (5_000.0, datetime.now(timezone.utc) - timedelta(days=1)))
    accounts = yaml.safe_load((_REPO / "config/accounts.yaml").read_text())["accounts"]
    rm = PropRiskManager.__new__(PropRiskManager)
    rm.account_name = "breakout_2"
    rm.account_id = "breakout_2"
    rm.dd_switch = dd.switch_for_account("breakout_2", accounts["breakout_2"])
    assert rm.check_dd_switch() is False
    assert dd.load_state()["breakout_2"]["read_state"] == dd.READ_CARRIED
