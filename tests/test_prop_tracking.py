"""PROP-TRACKING (operator 2026-10-12, verbatim: "we need to be able to track the prop trades - no upnl isn't a
solution to the issue"): the phone executor's Positions / Closed orders reads are stored, the account uPnL is the
venue's own per-position figures (MEASURED), the journal is reconciled against the venue list (close with the
reported exit / closed_unpriced; unconfirmed open + one alert), and a labelled mark ESTIMATE fills the gap until the
new APK reports. Real journal DB in tmp; no network (mark feed and Telegram stubbed)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from src.prop import phone_executor as pe
from src.prop import phone_reconcile as rc
from src.prop import prop_journal
from src.prop import prop_overview as po

TOKEN = "t" * 43
_DECLARED_MODE = rc.reconcile_mode   # the real YAML-backed mode, before the fixture pins `write`
ACCTS = {"breakout_2": {"account_class": "prop", "mode": "live"}}


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    dev = tmp_path / "devices.yaml"
    dev.write_text(yaml.safe_dump({"devices": [
        {"device_id": "p1", "account_id": "breakout_2", "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}]}))
    monkeypatch.setattr(pe, "DEVICES_PATH", dev)
    monkeypatch.setattr(pe, "_bybit_last", lambda sym: None)
    sent: list = []
    monkeypatch.setattr("src.runtime.notify.send_telegram_direct", lambda m, **k: sent.append(m) or True)
    monkeypatch.setattr("src.prop.breakout_notify.emit_prop_fill", lambda f, **k: {"push": False, "telegram": False})
    monkeypatch.setattr("src.runtime.local_pnl.last_mark_price", lambda *a, **k: None)
    # the repo's YAML declares breakout_2 `reconcile: report_only` (review of #17745); these tests exercise the
    # write path unless a test says otherwise (test_report_only_* below read the real declared mode)
    monkeypatch.setattr(rc, "reconcile_mode", lambda a: "write")
    return sent


def _dev():
    return pe.authenticate("Bearer " + TOKEN)


def _open(tid: str, symbol="ETHUSDT", direction="long", qty=1.1758, entry=2527.8, sl=2506.54, tp=2655.38,
          minutes_ago=60, status="open"):
    from src.prop.prop_report import ingest_report
    return ingest_report({"kind": "fill", "status": status, "account_id": "breakout_2", "ticket_id": tid,
                          "symbol": symbol, "direction": direction, "qty": qty, "entry_price": entry, "sl": sl,
                          "tp": tp, "opened_at": (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()})["id"]


def _status(positions, state="read", closed=None, **over):
    body = {"kind": "account_status", "equity": 5032.26, "balance": None, "equity_label": "portfolio",
            "positions_state": state}
    if positions is not None:
        body["positions"] = positions
    if closed is not None:
        body["closed_orders"] = closed
    body.update(over)
    return pe.record_report(_dev(), body)


ETH = {"symbol": "ETH", "side": "long", "qty": 1.1758, "entry": 2527.8, "mark": 2554.4, "upnl": 31.27}
SOL = {"symbol": "SOL", "side": "long", "qty": 17.26, "entry": 111.16, "mark": 112.3, "upnl": 19.67}


def _open_ids():
    fills = prop_journal.list_fills(account_id="breakout_2", limit=500)
    return sorted(f["id"] for f in po.split_fills(fills)[0])


# ---- ingest: storage + account uPnL ----

def test_clean_positions_read_is_stored_and_account_upnl_is_the_venue_sum():
    r = _status([ETH, SOL])
    row = prop_journal.latest_account_status("breakout_2")
    raw = json.loads(row["raw"])
    assert row["unrealized"] == 50.94 and raw["unrealized_source"] == "positions_sum"
    assert raw["provenance"] == "MEASURED" and raw["positions_state"] == "read"
    assert [(p["symbol"], p["direction"], p["upnl"]) for p in raw["positions"]] == [("ETHUSDT", "long", 31.27),
                                                                                   ("SOLUSDT", "long", 19.67)]
    assert r["reconcile"]["unconfirmed"] == ["ETHUSDT long", "SOLUSDT long"]   # no journal rows yet


def test_flat_read_is_measured_zero_and_partial_upnl_is_absent_not_summed():
    _status([])
    assert prop_journal.latest_account_status("breakout_2")["unrealized"] == 0.0
    _status([ETH, {**SOL, "upnl": None}])
    assert prop_journal.latest_account_status("breakout_2")["unrealized"] is None


def test_terminal_panel_unrealized_wins_and_equity_minus_balance_only_for_a_true_equity_label():
    _status([ETH], unrealized=40.0)
    raw = json.loads(prop_journal.latest_account_status("breakout_2")["raw"])
    assert raw["unrealized"] == 40.0 and raw["unrealized_source"] == "terminal_panel"
    _status(None, state="no_table", balance=5000.0, equity=5050.0, equity_label="equity")
    assert prop_journal.latest_account_status("breakout_2")["unrealized"] == 50.0
    _status(None, state="no_table", balance=5000.0, equity=5050.0, equity_label="portfolio")
    assert prop_journal.latest_account_status("breakout_2")["unrealized"] is None   # Portfolio != equity (unverified)


def test_unclean_read_stores_only_its_state_and_reconciles_nothing(env):
    jid = _open("prop-manual-x", minutes_ago=600)
    for st in ("no_table", "unparsed", "empty_unconfirmed", "count_mismatch"):
        r = _status([], state=st)
        raw = json.loads(prop_journal.latest_account_status("breakout_2")["raw"])
        assert raw["positions_state"] == st and "positions" not in raw and "reconcile" not in r
    assert _open_ids() == [jid] and env == []


@pytest.mark.parametrize("bad", [[{"symbol": "ETH", "qty": 1.0}], [{"symbol": "ETH", "side": "long", "qty": 0}],
                                 [{"symbol": "ETH", "side": "long", "qty": "1"}], "x", [1]])
def test_structurally_bad_positions_refuse_the_report(bad):
    with pytest.raises(ValueError):
        _status(bad)


def test_positions_miss_event_is_quiet_scrubbed_and_kept(env):
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "no_table",
                             "headers": ["Symbol", "Size ####"], "controls": ["Positions", "https://x.y/z"]})
    d = pe.last_positions_diag("breakout_2")
    assert d["state"] == "no_table" and d["headers"] == ["Symbol", "Size"] and "https" not in json.dumps(d)
    assert env == []


def test_positions_miss_no_tab_detail_is_kept_scrubbed(env):
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "no_tab d6 c0 t1",
                             "headers": ["Loading", "Menu"], "controls": ["Menu"]})
    d = pe.last_positions_diag("breakout_2")
    assert d["state"] == "no_tab d6 c0 t1" and d["headers"] == ["Loading", "Menu"]
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "no_tab https://x.y/z <b>1234567</b>"})
    assert "https" not in json.dumps(pe.last_positions_diag("breakout_2")) and "<" not in pe.last_positions_diag("breakout_2")["state"]


def test_positions_miss_unparsed_keeps_detail_and_masked_cells(env):
    # BREAKOUT-POS-UNPARSED: the first live miss said only "unparsed"; the app now names the missing keys and sends the
    # first failing row's cells as key:shape. Digits are masked again server-side (a raw value never lands on disk).
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "unparsed miss=side bad=0 n=1",
                             "headers": ["Market", "Side"], "cells": ["symbol:ETHUSD", "side:S", "value:1327.45 USD", "<x>"]})
    d = pe.last_positions_diag("breakout_2")
    assert d["state"] == "unparsed miss=side bad=0 n=1"
    assert d["cells"] == ["symbol:ETHUSD", "side:S", "value:dddd.dd USD", "x"]
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "no_table"})
    assert pe.last_positions_diag("breakout_2")["cells"] == []


# ---- reconcile ----

def test_matching_positions_change_nothing():
    a, b = _open("prop-manual-f79"), _open("prop-manual-903", symbol="SOLUSDT", qty=17.26, entry=111.16)
    r = _status([ETH, SOL])
    assert r["reconcile"]["matched"] == 2 and _open_ids() == sorted([a, b])


def test_absent_row_closes_only_after_two_reads_a_minute_apart_with_the_venue_exit(monkeypatch, env):
    jid = _open("prop-manual-f79")
    t0 = datetime.now(timezone.utc)
    tp_fill = {"symbol": "ETH", "side": "short", "qty": 1.1758, "price": 2655.4, "pnl": 150.03,
               "type": "Take profit", "status": "Filled",
               # derived from t0, never a literal date: the reconciler rejects fills older than the row minus TZ_SLACK
               "time": (t0 + timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S")}
    entry_fill = {"symbol": "ETH", "side": "long", "qty": 1.1758, "price": 2527.8, "status": "Filled"}
    monkeypatch.setattr(rc, "_now", lambda: t0)
    r1 = _status([], closed=[tp_fill, entry_fill])
    assert r1["reconcile"]["pending_close"] == [str(jid)] and _open_ids() == [jid]
    monkeypatch.setattr(rc, "_now", lambda: t0 + timedelta(seconds=30))
    _status([], closed=[tp_fill, entry_fill])
    assert _open_ids() == [jid]                        # two reads but < 60 s apart: still open
    monkeypatch.setattr(rc, "_now", lambda: t0 + timedelta(seconds=90))
    r3 = _status([], closed=[tp_fill, entry_fill])
    assert r3["reconcile"]["closed"] == [{"id": str(jid), "exit": 2655.4, "reason": "tp"}]
    assert _open_ids() == []
    close = [f for f in prop_journal.list_fills(account_id="breakout_2") if f["status"] == "closed"][0]
    assert (close["exit_price"], close["pnl"], close["ticket_id"]) == (2655.4, 150.03, "prop-manual-f79")
    assert json.loads(close["raw"])["provenance"] == rc.MEASURED
    assert env == []                                   # a priced close is the normal prop_closed notification


def test_a_present_read_resets_the_absence_count(monkeypatch):
    jid = _open("prop-manual-f79")
    t0 = datetime.now(timezone.utc)
    for i, pos in enumerate(([], [ETH], [])):
        monkeypatch.setattr(rc, "_now", lambda i=i: t0 + timedelta(seconds=90 * i))
        _status(pos)
    assert _open_ids() == [jid]


def test_absent_row_without_a_unique_exit_is_closed_unpriced_and_pinged(monkeypatch, env):
    jid = _open("prop-manual-f79")
    t0 = datetime.now(timezone.utc)
    for i in range(2):
        monkeypatch.setattr(rc, "_now", lambda i=i: t0 + timedelta(seconds=90 * i))
        r = _status([], closed=[])
    assert r["reconcile"]["closed_unpriced"] == [str(jid)] and _open_ids() == []
    close = [f for f in prop_journal.list_fills(account_id="breakout_2") if f["status"] == "closed"][0]
    assert close["exit_price"] is None and close["pnl"] is None and close["reason"] == "closed_unpriced"
    assert json.loads(close["raw"])["provenance"] == rc.UNVERIFIED
    assert len(env) == 1 and "without an exit price" in env[0]


def test_a_freshly_journaled_row_is_never_closed():
    jid = _open("prop-manual-new", minutes_ago=0)
    t0 = datetime.now(timezone.utc)
    rc.reconcile("breakout_2", [], [], now=t0)
    rc.reconcile("breakout_2", [], [], now=t0 + timedelta(seconds=61))
    assert _open_ids() == [jid]


def test_venue_position_without_a_journal_row_is_journaled_unconfirmed_once(env):
    _status([SOL])
    _status([SOL])
    rows = [f for f in prop_journal.list_fills(account_id="breakout_2") if f["status"] == "open"]
    assert len(rows) == 1
    assert rows[0]["ticket_id"].startswith("venue-breakout_2-SOLUSDT-long-") and rows[0]["reason"].startswith("unconfirmed")
    assert (rows[0]["qty"], rows[0]["entry_price"]) == (17.26, 111.16)
    assert len(env) == 1 and "UNCONFIRMED" in env[0]


def test_a_placed_working_order_on_the_venue_list_becomes_open_on_its_ticket(env):
    _open("prop-manual-lim", status="placed")
    r = _status([ETH])
    assert r["reconcile"]["opened"] == ["ETHUSDT long"] and r["reconcile"]["unconfirmed"] == []
    rows = [f for f in prop_journal.list_fills(account_id="breakout_2") if f["status"] == "open"]
    assert [(f["ticket_id"], f["sl"], f["tp"]) for f in rows] == [("prop-manual-lim", 2506.54, 2655.38)]
    assert env == []


def test_an_undeclared_instrument_is_flagged_once_and_never_journaled(env):
    _status([{"symbol": "DOGE", "side": "long", "qty": 10.0, "entry": 0.1}])
    _status([{"symbol": "DOGE", "side": "long", "qty": 10.0, "entry": 0.1}])
    assert prop_journal.list_fills(account_id="breakout_2") == []
    assert len(env) == 1 and "DOGE" in env[0]


def test_an_unmapped_venue_position_freezes_closes(monkeypatch):
    jid = _open("prop-manual-f79")
    t0 = datetime.now(timezone.utc)
    odd = {"symbol": "ETHUSD Perpetual", "side": "long", "qty": 1.1758, "entry": 2527.8}
    for i in range(3):
        monkeypatch.setattr(rc, "_now", lambda i=i: t0 + timedelta(seconds=90 * i))
        r = _status([odd])
    assert r["reconcile"]["pending_close"] == [str(jid)] and _open_ids() == [jid]


# ---- review of #17745 ----

def test_report_only_is_the_declared_mode_and_writes_nothing(monkeypatch, env):
    monkeypatch.setattr(rc, "reconcile_mode", _DECLARED_MODE)
    assert rc.reconcile_mode("breakout_2") == "report_only"           # config/prop_platforms.yaml declares it
    jid = _open("prop-manual-f79")
    t0 = datetime.now(timezone.utc)
    for i in range(4):
        monkeypatch.setattr(rc, "_now", lambda i=i: t0 + timedelta(seconds=90 * i))
        r = _status([SOL])                                            # ETH absent, SOL unjournaled
    assert r["reconcile"]["mode"] == "report_only"
    assert _open_ids() == [jid]                                       # nothing closed
    assert all(f["symbol"] != "SOLUSDT" for f in prop_journal.list_fills(account_id="breakout_2"))   # nothing opened
    would = [m for m in env if "REPORT-ONLY" in m]
    assert len([m for m in would if "close journal row" in m]) == 1   # ONE alert per decision, not per read
    assert len([m for m in would if "UNCONFIRMED open row" in m]) == 1


def test_a_closing_order_listed_before_the_row_went_missing_is_not_its_exit(monkeypatch):
    jid = _open("prop-manual-f79")
    old_close = {"symbol": "ETH", "side": "short", "qty": 1.1758, "price": 2400.0, "status": "Filled"}
    t0 = datetime.now(timezone.utc)
    monkeypatch.setattr(rc, "_now", lambda: t0)
    _status([ETH], closed=[old_close])                                # an earlier trade's close, listed while ETH is open
    for i in (1, 2):
        monkeypatch.setattr(rc, "_now", lambda i=i: t0 + timedelta(minutes=30 * i))
        r = _status([], closed=[old_close])
    assert r["reconcile"]["closed_unpriced"] == [str(jid)]            # never priced at the old close


def test_a_closing_order_timed_before_the_row_opened_is_not_its_exit(monkeypatch):
    jid = _open("prop-manual-f79", minutes_ago=60)
    stale = {"symbol": "ETH", "side": "short", "qty": 1.1758, "price": 2400.0, "status": "Filled",
             "time": "2026-01-01 00:00:00"}
    t0 = datetime.now(timezone.utc)
    for i in range(2):
        monkeypatch.setattr(rc, "_now", lambda i=i: t0 + timedelta(seconds=90 * i))
        r = _status([], closed=[stale])
    assert r["reconcile"]["closed_unpriced"] == [str(jid)]


def test_synthetic_id_is_per_position_lifetime_and_the_alert_is_not_repeated(monkeypatch, env):
    t0 = datetime.now(timezone.utc)
    monkeypatch.setattr(rc, "_now", lambda: t0)
    _status([SOL])
    first = [f["ticket_id"] for f in prop_journal.list_fills(account_id="breakout_2") if f["status"] == "open"]
    for i in (1, 2, 3):                                               # gone: closed after two reads
        monkeypatch.setattr(rc, "_now", lambda i=i: t0 + timedelta(minutes=5 * i))
        _status([])
    assert _open_ids() == []
    monkeypatch.setattr(rc, "_now", lambda: t0 + timedelta(minutes=30))
    _status([SOL])                                                    # same symbol + entry again: a NEW position
    opens = [f["ticket_id"] for f in prop_journal.list_fills(account_id="breakout_2") if f["status"] == "open"]
    assert len(opens) == 2 and opens[0] != opens[1] and first[0] in opens
    assert len(_open_ids()) == 1
    assert len([m for m in env if "is OPEN on the terminal" in m]) == 2   # one per position, not per read


def test_size_mismatch_is_alerted_once(monkeypatch, env):
    _open("prop-manual-f79")
    for _ in range(3):
        r = _status([{**ETH, "qty": 0.5}])
    assert r["reconcile"]["qty_mismatch"][0]["venue_qty"] == 0.5
    assert len([m for m in env if "size differs" in m]) == 1


def test_mark_source_is_derived_from_the_instrument_profile():
    assert po._mark_source("SOLUSDT") == "bybit_last_close_5m"
    assert po._mark_source("NOSUCHSYMBOL") == "bot_feed_last_close_5m"


# ---- overview ----

def _overview():
    return po.build_overview("7d", accounts=ACCTS)


def test_overview_per_trade_upnl_is_the_venue_position_measured():
    _open("prop-manual-f79")
    _open("prop-manual-903", symbol="SOLUSDT", qty=17.26, entry=111.16)
    _status([ETH, SOL])
    o = _overview()
    by = {t["symbol"]: t for t in o["open_trades"]}
    assert (by["ETHUSDT"]["unrealized_pnl"], by["ETHUSDT"]["unrealized_provenance"],
            by["ETHUSDT"]["unrealized_state"]) == (31.27, "measured", "venue_position")
    assert by["SOLUSDT"]["unrealized_pnl"] == 19.67
    acct = o["equity"]["accounts"][0]
    assert (acct["unrealized"], acct["unrealized_state"], acct["venue_open_positions"]) == (50.94, "venue_snapshot", 2)
    assert o["unrealized_pnl"] == 50.94


def test_overview_mark_estimate_is_labelled_and_kept_out_of_the_measured_total(monkeypatch):
    _open("prop-manual-f79")
    _open("prop-manual-903", symbol="SOLUSDT", qty=17.26, entry=111.16)
    _status(None, state="not_read")                    # today's APK: equity only
    marks = {"ETHUSDT": 2554.4, "SOLUSDT": 110.0}
    monkeypatch.setattr("src.runtime.local_pnl.last_mark_price", lambda s, **k: marks[s])
    o = _overview()
    by = {t["symbol"]: t for t in o["open_trades"]}
    assert (by["ETHUSDT"]["unrealized_pnl"], by["ETHUSDT"]["unrealized_provenance"],
            by["ETHUSDT"]["unrealized_state"]) == (round((2554.4 - 2527.8) * 1.1758, 2), "estimated", "mark_estimate")
    assert by["SOLUSDT"]["unrealized_pnl"] == round((110.0 - 111.16) * 17.26, 2)   # a loss stays negative
    acct = o["equity"]["accounts"][0]
    est = round(by["ETHUSDT"]["unrealized_pnl"] + by["SOLUSDT"]["unrealized_pnl"], 2)
    assert (acct["unrealized"], acct["unrealized_state"], acct["unrealized_provenance"]) == (est, "mark_estimate", "estimated")
    assert o["unrealized_pnl"] is None                 # an estimate never enters the measured total
    assert o["unrealized_pnl_incl_estimates"] == est and o["unrealized"]["accounts_estimated"] == 1


def test_short_mark_estimate_sign_and_no_mark_is_not_reported(monkeypatch):
    _open("prop-manual-s", direction="short", qty=2.0, entry=100.0)
    _status(None, state="not_read")
    monkeypatch.setattr("src.runtime.local_pnl.last_mark_price", lambda s, **k: 90.0)
    assert _overview()["open_trades"][0]["unrealized_pnl"] == 20.0
    monkeypatch.setattr("src.runtime.local_pnl.last_mark_price", lambda s, **k: None)
    o = _overview()
    assert (o["open_trades"][0]["unrealized_pnl"], o["open_trades"][0]["unrealized_state"]) == (None, "not_reported")
    assert o["equity"]["accounts"][0]["unrealized_state"] == "not_reported"


# ---- FILL-IN of rows already closed unpriced (PHONE-POSITIONS-DIV; writes even while report_only) ----

def _unpriced(tid, symbol, direction, qty, entry, hours_ago_open=20, hours_ago_close=6):
    from src.prop.prop_report import ingest_report
    n = datetime.now(timezone.utc)
    return ingest_report({"kind": "fill", "status": "closed", "account_id": "breakout_2", "ticket_id": tid,
                          "symbol": symbol, "direction": direction, "qty": qty, "entry_price": entry,
                          "exit_price": None, "pnl": None, "provenance": "UNVERIFIED", "reason": "closed_unpriced",
                          "opened_at": (n - timedelta(hours=hours_ago_open)).isoformat(),
                          "closed_at": (n - timedelta(hours=hours_ago_close)).isoformat()})["id"]


def _vt(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _co(symbol, side, qty, price, pnl, hours_ago, status="Filled", typ="Stop"):
    return {"symbol": symbol, "side": side, "qty": qty, "price": price, "pnl": pnl, "type": typ, "status": status,
            "time": _vt(hours_ago)}


def _fills():
    return {f["ticket_id"]: f for f in prop_journal.list_fills(account_id="breakout_2", limit=500)
            if str(f.get("status")).lower() == "closed"}


def test_fill_in_prices_both_unpriced_closes_while_reconcile_is_report_only(monkeypatch):
    monkeypatch.setattr(rc, "reconcile_mode", _DECLARED_MODE)
    assert rc.reconcile_mode("breakout_2") == "report_only"
    _unpriced("t-sol", "SOLUSDT", "long", 17.26263867, 111.16)
    _unpriced("t-eth", "ETHUSDT", "long", 1.17577895, 2527.8, hours_ago_close=2)
    r = _status([], closed=[_co("SOL", "short", 17.26, 111.39, 3.97, 6), _co("ETH", "short", 1.1758, 2531.2, 3.99, 2),
                            _co("SOL", "long", 17.26, 111.16, None, 20, typ="Limit")])
    assert sorted(x["exit"] for x in r["fill_in"]["filled"]) == [111.39, 2531.2]
    f = _fills()
    assert (f["t-sol"]["exit_price"], f["t-sol"]["pnl"]) == (111.39, 3.97)
    assert (f["t-eth"]["exit_price"], f["t-eth"]["pnl"]) == (2531.2, 3.99)
    assert json.loads(f["t-sol"]["raw"])["provenance"].lower() == "measured"
    assert len(_fills()) == 2   # corrected in place, not appended


def test_closed_row_with_only_a_net_pnl_column_still_carries_the_venue_pnl():
    rows = rc.clean_closed("breakout_2", [{"symbol": "SOL", "side": "short", "qty": 17.26, "price": 111.39, "netpnl": 3.97}])
    assert rows[0]["pnl"] == 3.97


def test_fill_in_does_not_match_the_wrong_side_size_time_or_a_cancelled_order(monkeypatch):
    monkeypatch.setattr(rc, "reconcile_mode", _DECLARED_MODE)
    _unpriced("t-sol", "SOLUSDT", "long", 17.26263867, 111.16)
    r = _status([], closed=[_co("SOL", "long", 17.26, 112.0, 1.0, 6),                      # same side: an entry
                            _co("SOL", "short", 9.0, 112.0, 1.0, 6),                       # size off
                            _co("SOL", "short", 17.26, 112.0, 1.0, 60),                    # before the row opened
                            _co("SOL", "short", 17.26, 112.0, 1.0, 6, status="Cancelled")])
    assert r["fill_in"]["filled"] == [] and r["fill_in"]["no_match"]
    assert _fills()["t-sol"]["exit_price"] is None


def test_fill_in_with_two_candidates_writes_nothing_and_pings_once(env, monkeypatch):
    monkeypatch.setattr(rc, "reconcile_mode", _DECLARED_MODE)
    _unpriced("t-sol", "SOLUSDT", "long", 17.26263867, 111.16)
    co = [_co("SOL", "short", 17.26, 111.39, 3.9, 6), _co("SOL", "short", 17.26, 110.0, -20.0, 5)]
    r = _status([], closed=co)
    assert r["fill_in"]["filled"] == [] and len(r["fill_in"]["ambiguous"]) == 1
    _status([], closed=co)
    assert _fills()["t-sol"]["exit_price"] is None
    assert len([m for m in env if "could be the exit" in m]) == 1


def test_fill_in_never_runs_on_an_unclean_closed_read():
    _unpriced("t-sol", "SOLUSDT", "long", 17.26263867, 111.16)
    r = _status([], closed=None)    # the phone sends closed_orders only when its Closed read was `read`
    assert r["fill_in"] == {"filled": [], "ambiguous": [], "no_match": []}
    assert _fills()["t-sol"]["exit_price"] is None


# ---- Closed orders: live layout (operator screenshot 2026-10-13): reduce flag, Filled vs Cancelled pair ----

def _live_co(side, typ, status, price, reduce=True, qty=0.571, hours_ago=1):
    return {**_co("ETH", side, qty, price, None, hours_ago, status=status, typ=typ), "reduce": reduce}


def test_clean_closed_keeps_the_reduce_flag_and_only_a_bool():
    rows = rc.clean_closed("breakout_2", [{"symbol": "ETH", "side": "long", "reduce": True, "price": 1},
                                           {"symbol": "ETH", "side": "long", "reduce": "yes", "price": 1}])
    assert [r["reduce"] for r in rows] == [True, None]


def test_fill_in_takes_the_filled_reduce_row_of_a_tp_sl_pair_and_ignores_the_entry_row():
    _unpriced("t-eth", "ETHUSDT", "short", 0.571, 2491.47)
    r = _status([], closed=[_live_co("long", "Stop loss", "Filled", 2535.25),
                            _live_co("long", "Take profit", "Cancelled", 2244.81),
                            _live_co("long", "Limit", "Filled", 2490.0, reduce=False)])   # a long ENTRY, not an exit
    assert [x["exit"] for x in r["fill_in"]["filled"]] == [2535.25]
    assert _fills()["t-eth"]["exit_price"] == 2535.25


def test_fill_in_overrides_an_inferred_close_with_the_measured_exit():
    from src.prop import fill_inference as fi
    from src.prop.prop_report import ingest_report
    ingest_report({"kind": "fill", "status": "open", "account_id": "breakout_2", "ticket_id": "t-inf", "symbol": "ETHUSDT",
                   "direction": "short", "qty": 0.571, "entry_price": 2491.47, "sl": 2535.25, "tp": 2244.81,
                   "external_order_id": "inferred-t-inf", "source": fi.SOURCE, "provenance": "estimated", "inferred": True,
                   "opened_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()})
    ingest_report({"kind": "fill", "status": "closed", "account_id": "breakout_2", "ticket_id": "t-inf", "symbol": "ETHUSDT",
                   "direction": "short", "qty": 0.571, "entry_price": 2491.47, "exit_price": 2535.25,
                   "external_order_id": "inferred-t-inf", "source": fi.SOURCE, "provenance": "estimated", "inferred": True,
                   "closed_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()})
    r = _status([], closed=[_live_co("long", "Stop loss", "Filled", 2536.1)])
    assert [x["exit"] for x in r["fill_in"]["filled"]] == [2536.1]
    c = _fills()["t-inf"]
    assert c["exit_price"] == 2536.1 and json.loads(c["raw"])["provenance"].lower() == "measured"
    assert len(_fills()) == 1


# --- BREAKOUT-UNTRACKED-1014: a Positions miss while the journal holds a ticket ON THE VENUE alerts ONCE ---

def _venue_ticket(tid: str, status: str = "placed"):
    prop_journal.record_ticket({
        "ticket_id": tid, "account_id": "breakout_2", "strategy": "trend_donchian_eth_prop", "symbol": "ETHUSDT",
        "direction": "short", "entry": 2458.24, "sl": 2504.25, "tp": 2214.87, "qty": 0.54,
        "valid_until": datetime.now(timezone.utc).isoformat(), "status": status})


def _unseen_pings(sent):
    return [m for m in sent if "cannot read the Breakout Positions tab" in m]


def test_positions_miss_with_a_placed_ticket_alerts_once_per_ticket(env):
    _venue_ticket("prop-manual-f6552263ed1d")
    for _ in range(3):
        pe.record_event(_dev(), {"event": "positions_miss", "reason": "no_tab d6 c6 t1"})
    pings = _unseen_pings(env)
    assert len(pings) == 1
    assert "prop-manual-f6552263ed1d" in pings[0] and "INVISIBLE" in pings[0] and "no_tab d6 c6 t1" in pings[0]
    _venue_ticket("t-second", status="filled")      # a NEW unseen ticket alerts once more, naming only itself
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "unparsed"})
    pings = _unseen_pings(env)
    assert len(pings) == 2 and "t-second" in pings[1] and "f6552263ed1d" not in pings[1]


def test_positions_miss_without_a_venue_ticket_stays_quiet(env):
    _venue_ticket("t-closed", status="closed")
    _venue_ticket("t-emitted", status="emitted")
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "no_tab d6 c6 t1"})
    assert _unseen_pings(env) == []


def test_closed_orders_miss_never_raises_the_unseen_alert(env):
    _venue_ticket("t1")
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "closed_no_table"})
    assert _unseen_pings(env) == []


def test_amend_control_dump_is_kept_and_never_raises_the_unseen_alert(env):
    # BREAKOUT-POS-UNPARSED: the trail amend FOUND the row but refuses (edit control unmeasured) and posts the row's
    # control dump through positions_miss: it is kept as the visible diag and is not an "invisible position" alert.
    _venue_ticket("t1")
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "amend_no_edit_unmeasured",
                             "headers": ["Market", "TP / SL"], "controls": ["TP / SL:button Edit", "  cls=icon-btn"]})
    assert _unseen_pings(env) == []
    d = pe.last_positions_diag("breakout_2")
    assert d["state"] == "amend_no_edit_unmeasured" and d["controls"] == ["TP / SL:button Edit", "cls=icon-btn"]


# ---- side_unread (BREAKOUT-POS-UNPARSED, 1b.193: the Side cell has no text) ----

# entry is the VENUE's avg opening price (operator screenshot 21:41Z), 0.13% off the journal's 2458.24
SIDELESS = {"symbol": "ETH", "qty": 0.5449, "entry": 2461.52, "mark": 2436.1, "upnl": 12.34}


def test_side_unread_takes_the_side_from_the_one_open_fill_and_is_estimated(env):
    _open("prop-manual-f6552263ed1d", direction="short", qty=0.54, entry=2458.24, sl=2504.25, tp=2214.87)
    _status([SIDELESS], state="side_unread")
    raw = json.loads(prop_journal.latest_account_status("breakout_2")["raw"])
    assert raw["positions_state"] == "read" and raw["positions_side_source"] == "journal"
    assert raw["positions_provenance"] == "ESTIMATED"
    assert raw["positions"][0]["direction"] == "short" and raw["positions"][0]["side_source"] == "journal"


@pytest.mark.parametrize("fills", [[], [("short", 2600.0, 0.54)], [("short", 2458.24, 0.54), ("long", 2458.5, 0.54)],
                                   [("short", 2458.24, 1.20)]])
def test_side_unread_without_exactly_one_side_stays_unreconciled(env, fills):
    # no fill / price outside 0.5% / both sides / size differs (1.20 vs the venue's 0.5449): never matched
    for i, (d, e, q) in enumerate(fills):
        _open(f"t{i}", direction=d, entry=e, qty=q)
    r = _status([SIDELESS], state="side_unread")
    raw = json.loads(prop_journal.latest_account_status("breakout_2")["raw"])
    assert raw["positions_state"] == "side_unread" and "positions" not in raw and "reconcile" not in r


def test_unseen_alert_retries_when_the_ping_failed(env, monkeypatch):
    _venue_ticket("t1")
    monkeypatch.setattr("src.runtime.notify.send_telegram_direct", lambda m, **k: False)
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "no_tab d6 c6 t1"})
    monkeypatch.setattr("src.runtime.notify.send_telegram_direct", lambda m, **k: env.append(m) or True)
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "no_tab d6 c6 t1"})
    assert len(_unseen_pings(env)) == 1


# ---- position sheet (BREAKOUT-SL-AMEND-CLICK: side / volume / avg entry read off the row's sheet) ----

SHEET_ROW = {"symbol": "ETH", "side": "short", "side_source": "sheet", "qty": 0.54, "qty_src": "sheet", "entry": 2461.52,
             "mark": 2440.65, "upnl": None, "upnl_signed": False}


def test_sheet_side_is_measured_and_an_unsigned_upnl_stays_null(env):
    _open("prop-manual-f6552263ed1d", direction="short", qty=0.54, entry=2458.24, sl=2504.25, tp=2214.87)
    _status([SHEET_ROW], state="read")
    raw = json.loads(prop_journal.latest_account_status("breakout_2")["raw"])
    assert raw["positions_state"] == "read" and raw["positions_side_source"] == "sheet"
    assert raw["positions_provenance"] == "MEASURED"
    p = raw["positions"][0]
    assert (p["direction"], p["qty"], p["entry"], p["side_source"], p["upnl"], p["upnl_signed"]) == \
        ("short", 0.54, 2461.52, "sheet", None, False)
    # a sign-less uPnL is never summed into the account's unrealized as a measured number
    assert raw.get("unrealized_source") != "positions_sum"


def test_sheet_diag_is_kept_and_never_raises_the_unseen_alert(env):
    _venue_ticket("t1")
    for reason in ("sheet_sheet_not_open", "amend_sheet_unmeasured", "sheet_ok"):
        pe.record_event(_dev(), {"event": "positions_miss", "reason": reason, "headers": ["child:none", "group:clicked"],
                                 "controls": ["stop px=1 pen=1", "  button P ✎"]})
        assert pe.last_sheet_diag("breakout_2")["state"] == reason   # BREAKOUT-NATIVE-TAP: its own slot
    assert _unseen_pings(env) == []


def test_sheet_diag_has_its_own_slot(env):
    # BREAKOUT-NATIVE-TAP finding (b): 2026-10-15 08:21:38Z the sheet probe (sheet_sheet_not_open) was overwritten one
    # second later by the Positions read's own side_unread miss. Sheet / child probes now land in a separate slot.
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "sheet_sheet_not_open",
                             "headers": ["child/native:touched k=2.63", "child/native:no_sheet"], "controls": ["up: span"]})
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "side_unread miss=side bad=0 n=1", "headers": ["Market"]})
    assert pe.last_sheet_diag("breakout_2")["state"] == "sheet_sheet_not_open"
    assert pe.last_sheet_diag("breakout_2")["headers"][0].startswith("child/native:touched")
    assert pe.last_positions_diag("breakout_2")["state"] == "side_unread miss=side bad=0 n=1"
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "amend_sheet_unmeasured"})
    pe.record_event(_dev(), {"event": "positions_miss", "reason": "child_side_unread"})
    assert pe.last_sheet_diag("breakout_2")["state"] == "child_side_unread"
    assert pe.last_positions_diag("breakout_2")["state"] == "side_unread miss=side bad=0 n=1"


def test_row_side_read_off_the_expanded_child_row_is_measured(env):
    # BREAKOUT-NATIVE-TAP finding (a): the expanded child row shows the side as TEXT; the phone labels it side_source row
    _open("prop-manual-f6552263ed1d", direction="short", qty=0.54, entry=2458.24, sl=2504.25, tp=2214.87)
    _status([{"symbol": "ETH", "side": "short", "side_source": "row", "qty": 0.54, "entry": 2461.52, "mark": 2440.65,
              "upnl": 20.88}], state="read")
    raw = json.loads(prop_journal.latest_account_status("breakout_2")["raw"])
    assert (raw["positions_state"], raw["positions_side_source"], raw["positions_provenance"]) == ("read", "row", "MEASURED")
    assert raw["positions"][0]["side_source"] == "row" and "upnl_signed" not in raw["positions"][0]
