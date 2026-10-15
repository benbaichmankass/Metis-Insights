"""The trader-restart ping (checklist row TELEGRAM-RESTART-MSG, 2026-10-15).

Contracts under test:

1. clean + planned cause (deploy) -> ONE compact two-line plain message, ✅.
2. a ghost / an untracked position / a missing broker SL or TP -> ❌ with the
   problem count, full sectioned HTML (expandable blockquotes), each named.
3. an account or bracket we could not read renders "— (could not read)" and
   counts as a problem — never 0.
4. an unplanned cause (crash) -> full message even with a clean reconcile.
5. only legs WITH open packages are listed; dry accounts are not reconciled.
6. the claim "Bybit holds SL/TP at the broker for every open position" is gone.
7. restart_cause: marker + systemd journal + uptime are combined, never guessed.
"""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from src.runtime import boot_audit as ba
from src.runtime import restart_cause as rc


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _journal(tmp_path, trades=(), packages=()):
    db = tmp_path / "trade_journal.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT, "
                  "status TEXT, is_backtest INTEGER, strategy_name TEXT, account_id TEXT)")
        c.execute("CREATE TABLE order_packages (order_package_id TEXT, strategy_name TEXT, "
                  "status TEXT, linked_trade_id INTEGER)")
        for t in trades:
            c.execute("INSERT INTO trades VALUES (?,?,?,?,0,?,?)", t)
        for p in packages:
            c.execute("INSERT INTO order_packages VALUES (?,?,?,?)", p)
    return str(db)


ACCTS = {
    "bybit_2": {"account_id": "bybit_2", "exchange": "bybit", "mode": "live",
                "account_class": "real_money"},
    "bybit_1": {"account_id": "bybit_1", "exchange": "bybit", "mode": "live",
                "account_class": "paper"},
    "alpaca_paper": {"account_id": "alpaca_paper", "exchange": "alpaca", "mode": "live",
                     "account_class": "paper"},
    "breakout_2": {"account_id": "breakout_2", "exchange": "breakout", "mode": "live",
                   "account_class": "prop"},
}


def _bybit_payload(sl=True, tp=True, unchecked=()):
    return {"positions": [{"symbol": "BTCUSDT", "side": "Buy", "size": 0.01,
                           "stop_loss": 79000.0 if sl else None,
                           "take_profit": 82000.0 if tp else None}],
            "orders": [], "order_symbols_unchecked": list(unchecked)}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """bybit_2: 1 long BTC, tracked, full bracket. alpaca_paper: 1 long SPY,
    tracked, stop + limit resting. bybit_1: flat. breakout_2: no read path."""
    db = _journal(tmp_path,
                  trades=[(1, "BTCUSDT", "long", "open", "btc_trend_1h", "bybit_2"),
                          (2, "SPY", "long", "open", "spy_trend_1d", "alpaca_paper")],
                  packages=[("p1", "btc_trend_1h", "open", 1),
                            ("p2", "spy_trend_1d", "open", 2),
                            ("p3", "never_placed", "open", None)])
    monkeypatch.setattr("src.utils.paths.trade_journal_db_path", lambda: db)
    monkeypatch.setattr(ba, "_load_live_accounts", lambda: dict(ACCTS))
    state = {
        "positions": {"bybit_2": [{"symbol": "BTCUSDT", "side": "Buy", "size": 0.01}],
                      "bybit_1": [],
                      "alpaca_paper": [{"symbol": "SPY", "side": "long", "size": 10}]},
        "bybit": {"bybit_2": _bybit_payload(), "bybit_1": _bybit_payload()},
        "alpaca": {"orders": [{"symbol": "SPY", "side": "sell", "order_type": "stop"},
                              {"symbol": "SPY", "side": "sell", "order_type": "limit"}]},
    }
    import src.units.accounts.clients as cl
    monkeypatch.setattr(cl, "account_open_positions",
                        lambda cfg: state["positions"].get(cfg["account_id"]))
    monkeypatch.setattr(cl, "account_bybit_open_orders",
                        lambda cfg: state["bybit"].get(cfg["account_id"]))
    monkeypatch.setattr(cl, "account_alpaca_open_orders", lambda cfg: state["alpaca"])
    monkeypatch.setattr(cl, "account_ib_open_orders", lambda cfg: None)
    return state


DEPLOY = rc.RestartCause("deploy", "deploy a1b2c3d (#18216)")
CRASH = rc.RestartCause("crash", "crash / unclean exit of the previous run (systemd)")


def _render(cause=DEPLOY):
    return ba.render_restart_message(ba.build_restart_report(cause=cause))


# ---------------------------------------------------------------------------
# message contracts
# ---------------------------------------------------------------------------

def test_clean_deploy_is_one_compact_message(world):
    text, mode, full = _render()
    assert not full and mode is None
    lines = text.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("✅ Trader restarted — all live trades accounted for: 2/2")
    assert "SL+TP at broker 2/2" in lines[0]
    assert "1 prop account(s) not broker-readable" in lines[0]
    assert lines[1] == "cause: deploy a1b2c3d (#18216)"


def test_ghost_flips_headline_and_is_named(world):
    world["positions"]["bybit_2"] = []
    world["bybit"]["bybit_2"] = {"positions": [], "orders": []}
    text, mode, full = _render()
    assert full and mode == "HTML"
    assert text.startswith("<b>❌ Trader restarted — 1 problem(s)")
    assert text.split("\n")[1] == "cause: deploy a1b2c3d (#18216)"
    assert "ghost: trade #1 BTCUSDT long (journal open, broker flat)" in text
    assert "<blockquote expandable>" in text


def test_untracked_position_counts(world):
    world["positions"]["bybit_1"] = [{"symbol": "ETHUSDT", "side": "Sell", "size": 1}]
    world["bybit"]["bybit_1"] = {"positions": [{"symbol": "ETHUSDT", "side": "Sell",
                                                "stop_loss": 1.0, "take_profit": 2.0}],
                                 "orders": []}
    text, _, full = _render()
    assert full and "1 problem(s)" in text
    assert "untracked: ETHUSDT short (broker open, no journal row)" in text


def test_missing_tp_is_measured_not_claimed(world):
    world["bybit"]["bybit_2"] = _bybit_payload(tp=False)
    text, _, full = _render()
    assert full and "BTCUSDT long: no TP at broker" in text
    assert "Bybit holds SL/TP" not in text


def test_partial_bybit_leg_on_orders_counts_as_target(world):
    world["bybit"]["bybit_2"] = _bybit_payload(tp=False)
    world["bybit"]["bybit_2"]["orders"] = [
        {"symbol": "BTCUSDT", "side": "Sell", "stop_order_type": "PartialTakeProfit"}]
    _, _, full = _render()
    assert not full


def test_unread_positions_are_could_not_read_not_zero(world):
    world["positions"]["alpaca_paper"] = None
    text, _, full = _render()
    assert full and "alpaca_paper: broker positions — (could not read)" in text
    assert "1 problem(s)" in text


def test_unread_brackets_are_a_problem(world):
    world["bybit"]["bybit_2"] = _bybit_payload(unchecked=["BTCUSDT/Order"])
    text, _, full = _render()
    assert full and "BTCUSDT long: SL/TP at broker — (could not read)" in text


def test_unplanned_cause_sends_full_message_even_when_clean(world):
    text, mode, full = _render(cause=CRASH)
    assert full and mode == "HTML"
    assert text.startswith("<b>❌ Trader restarted — 1 problem(s)")
    assert "cause: crash" in text.split("\n")[1]


def test_sections_by_venue_list_only_legs_with_packages(world):
    text, _, _ = _render(cause=rc.RestartCause("unknown", "unknown"))
    assert "Bybit live —" in text and "Alpaca paper —" in text and "Prop —" in text
    assert text.index("Bybit live") < text.index("Prop") < text.index("Bybit paper")
    assert "btc_trend_1h: 1 open package(s)" in text
    assert "never_placed" not in text            # unlinked package: no trade placed
    assert "breakout_2: broker — (no broker read from the trader)" in text
    assert "cause: unknown" in text


def test_send_routes_restart_kind_and_never_raises(world, monkeypatch):
    sent = []
    monkeypatch.setattr(rc, "determine", lambda: DEPLOY)
    monkeypatch.setattr("src.runtime.telegram_channels.send",
                        lambda kind, text, parse_mode=None: sent.append((kind, parse_mode)))
    rep = ba.send_restart_report_on_boot()
    assert sent == [("restart", None)] and rep["full"] is False


def test_send_survives_everything_failing(monkeypatch):
    monkeypatch.setattr(ba, "build_restart_report", lambda: 1 / 0)
    assert ba.send_restart_report_on_boot() is None


# ---------------------------------------------------------------------------
# restart_cause
# ---------------------------------------------------------------------------

STOP = ["Stopping ICT Trader...", "ict-trader-live.service: Deactivated successfully.",
        "Stopped ICT Trader.", "Started ICT Trader."]
CRASHED = ["Started ICT Trader.", "2026 INFO tick",
           "ict-trader-live.service: Main process exited, code=exited, status=1/FAILURE",
           "ict-trader-live.service: Failed with result 'exit-code'.",
           "ict-trader-live.service: Scheduled restart job, restart counter is at 1.",
           "Started ICT Trader."]
OOM = ["Started ICT Trader.",
       "ict-trader-live.service: A process of this unit has been killed by the OOM killer.",
       "ict-trader-live.service: Failed with result 'oom-kill'.", "Started ICT Trader."]


def test_classify_previous_exit():
    assert rc.classify_previous_exit(STOP) == "clean_stop"
    assert rc.classify_previous_exit(CRASHED) == "crash"
    assert rc.classify_previous_exit(OOM) == "oom"
    assert rc.classify_previous_exit([]) is None
    # the trader's OWN log text never classifies an exit
    assert rc.classify_previous_exit(
        ["Started X.", "INFO Failed with result 'exit-code' in a log line", "Started X."]) is None


def test_deploy_marker_plus_clean_stop_is_deploy(tmp_path):
    rc.write_intent("deploy", tmp_path, sha="a" * 40, **{"from": ""})
    c = rc.determine(tmp_path, journal=STOP, uptime_s=99999)
    assert c.cause == "deploy" and c.planned and c.line().startswith("cause: deploy aaaaaaa")
    # consumed: a second start does not inherit it
    assert rc.determine(tmp_path, journal=STOP, uptime_s=99999).cause == "unknown"


def test_crash_wins_over_a_deploy_marker(tmp_path):
    rc.write_intent("deploy", tmp_path, sha="b" * 40)
    assert rc.determine(tmp_path, journal=CRASHED, uptime_s=99999).cause == "crash"


def test_stale_marker_is_ignored(tmp_path):
    p = rc.intent_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"cause": "manual", "at": int(time.time()) - 3600}))
    assert rc.determine(tmp_path, journal=None, uptime_s=99999).cause == "unknown"


def test_vm_reboot_and_unreadable_journal(tmp_path):
    assert rc.determine(tmp_path, journal=None, uptime_s=120).cause == "vm_reboot"
    c = rc.determine(tmp_path, journal=None, uptime_s=99999)
    assert c.cause == "unknown" and c.line() == "cause: unknown"


def test_manual_and_watchdog_markers(tmp_path):
    rc.write_intent("manual", tmp_path, by="restart-bot-service")
    assert rc.determine(tmp_path, journal=STOP, uptime_s=99999).line() == \
        "cause: manual restart (restart-bot-service)"
    rc.write_intent("watchdog_autoheal", tmp_path)
    c = rc.determine(tmp_path, journal=STOP, uptime_s=99999)
    assert c.cause == "watchdog_autoheal" and not c.planned


def test_report_thread_never_holds_boot_past_the_wait(monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(ba, "send_restart_report_on_boot", lambda: gate.wait(5))
    t0 = time.time()
    t = ba.start_restart_report_thread(wait_s=0.2)
    assert time.time() - t0 < 2 and t is not None and t.is_alive()
    gate.set()
