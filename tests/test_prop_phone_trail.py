"""PROP-TRAIL-PHONE: the server half of the phone-executed trail (src/prop/phone_trail.py).

Plan computation reuses prop_trail.plan_trail; the claim channel serves ONE amend per ticket, only to an app
that declares it. An unverifiable result fails THAT attempt only: backoff, retry, ONE red flag after
FLAG_AFTER in a row, recovery announced once (operator directive 2026-10-09: "There is no halting.").
Only a human-moved stop holds the trail, until the human's levels are on record."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.prop import phone_executor as pe
from src.prop import phone_trail as ptr
from src.prop import prop_journal

TOKEN = "t" * 43
LEG = {"timeframe": "1h", "atr_stop_mult": 2.5, "trail_mult": 3.5, "tp_r": 6.0}
LEGS = {"trend_donchian_eth_prop": LEG}
SIG = datetime(2026, 10, 7, 10, 5, tzinfo=timezone.utc)   # forming leg: managed bars start 11:00


@pytest.fixture(autouse=True)
def _iso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    dev = tmp_path / "devices.yaml"
    dev.write_text(yaml.safe_dump({"devices": [
        {"device_id": "p1", "account_id": "breakout_2", "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}]}))
    monkeypatch.setattr(pe, "DEVICES_PATH", dev)
    acc = tmp_path / "accounts.yaml"
    acc.write_text(yaml.safe_dump({"accounts": {"breakout_2": {"mode": "live"}}}))
    monkeypatch.setattr(pe, "ACCOUNTS_PATH", acc)
    monkeypatch.delenv("PROP_PHONE_MODE_BREAKOUT_2", raising=False)
    monkeypatch.setattr(pe, "_unserved_dry_test_request", lambda acct: None)  # no live dry-test side effect
    sent: list = []
    monkeypatch.setattr("src.runtime.notify.send_telegram_direct", lambda m, **k: sent.append(m) or True)
    import src.prop.prop_trail as pt
    monkeypatch.setattr(pt, "_load_legs", lambda *a, **k: LEGS)
    return sent


def _dev() -> pe.PhoneDevice:
    return pe.authenticate("Bearer " + TOKEN)


def _parent(tid: str = "T1", *, status: str = "placed", meta=None, direction: str = "long") -> None:
    long_ = direction == "long"
    prop_journal.record_ticket({
        "ticket_id": tid, "account_id": "breakout_2", "strategy": "trend_donchian_eth_prop", "symbol": "ETHUSDT",
        "direction": direction, "entry": 2500.0, "sl": 2450.0 if long_ else 2550.0, "tp": 2800.0 if long_ else 2200.0,
        "qty": 0.5, "signal_time": SIG.isoformat(), "valid_until": (SIG + timedelta(minutes=10)).isoformat(),
        "status": status, "meta": meta})


def _candles(highs, *, start=datetime(2026, 10, 7, 11, 0, tzinfo=timezone.utc), last_close=None):
    rows = []
    for i, h in enumerate(highs):
        c = h - 5
        rows.append({"timestamp": start + timedelta(hours=i), "open": c, "high": h, "low": c - 10, "close": c})
    if last_close is not None:
        rows[-1]["close"] = last_close
    return pd.DataFrame(rows)


# ATR = |2500-2450| / 2.5 = 20; trail = ext - 3.5*20 = ext - 70
UP = _candles([2520, 2560, 2600])            # ext 2600 -> trail 2530, closes 2515/2555/2595
NOW = datetime(2026, 10, 7, 14, 1, tzinfo=timezone.utc)


def _trail(tid="T1"):
    return (prop_journal.get_ticket(tid)["meta"] or {}).get(ptr.KEY) or {}


def test_plan_emits_one_sl_amend_from_the_shared_replay():
    _parent()
    out = ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: UP)
    am = _trail()["amend"]
    assert out["emitted"] == ["T1#a1"]
    assert am["status"] == "emitted" and am["kind"] == "sl"
    assert am["from_sl"] == 2450.0 and am["sl"] == pytest.approx(2530.0) and am["tp"] == 2800.0
    # same replay as the VM trail: plan_trail on the same inputs gives the same stop
    from src.prop.prop_trail import plan_trail
    p = plan_trail(leg=LEG, direction="long", entry=2500.0, initial_sl=2450.0, signal_time=SIG,
                   resting_sl=2450.0, candles=UP, now=NOW, price_step=0.01)
    assert p.sl == pytest.approx(am["sl"])


def test_one_outstanding_and_once_per_closed_bar():
    _parent()
    calls = []
    fn = lambda s, tf: calls.append(1) or UP  # noqa: E731
    ptr.plan_amends("breakout_2", now=NOW, candles_fn=fn)
    ptr.plan_amends("breakout_2", now=NOW + timedelta(minutes=5), candles_fn=fn)
    assert len(calls) == 1 and _trail()["seq"] == 1
    # emitted amend still valid next bar: no second amend
    ptr.plan_amends("breakout_2", now=NOW + timedelta(minutes=10), candles_fn=fn)
    assert _trail()["seq"] == 1


def test_no_amend_when_not_tighter_or_through_price():
    _parent()
    ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: _candles([2505, 2508, 2510]))
    assert not _trail().get("amend")  # trail 2440 < resting 2450
    _parent("T2")
    ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: _candles([2520, 2560, 2600], last_close=2531))
    assert not _trail("T2").get("amend")  # 2530 within the buffer of the price 2531


def test_test_tickets_and_unplaced_tickets_are_not_planned():
    _parent("TT", meta={"test": True})
    _parent("TE", status="emitted")
    out = ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: UP)
    assert out["emitted"] == []


def test_close_lever_alerts_once_never_acts(_iso):
    LEGS["trend_donchian_eth_prop"] = {**LEG, "stale_exit_bars": 2, "stale_exit_below_r": 5.0}
    try:
        _parent()
        ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: UP)
        ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=1), candles_fn=lambda s, tf: UP)
        assert sum("stale_exit_bars" in m for m in _iso) == 1
    finally:
        LEGS["trend_donchian_eth_prop"] = LEG


def test_claim_serves_amend_only_to_an_app_that_accepts_it():
    _parent()
    ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: UP)
    assert pe.claim_next(_dev(), now=NOW) is None             # old app: never an amend
    got = ptr.claim_amend(_dev(), now=NOW)
    assert got["kind"] == "amend" and got["amend_id"] == "T1#a1" and got["venue_symbol"] == "ETHUSD"
    assert got["from_sl"] == 2450.0 and got["sl"] == pytest.approx(2530.0) and got["submit"] == "live"
    assert ptr.claim_amend(_dev(), now=NOW) is None           # one claim per amend


def test_entry_tickets_are_claimed_before_amends(monkeypatch):
    _parent()
    ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: UP)
    prop_journal.record_ticket({"ticket_id": "E1", "account_id": "breakout_2", "strategy": "trend_donchian_eth_prop",
                                "symbol": "ETHUSDT", "direction": "long", "entry": 2500, "sl": 2450, "tp": 2800,
                                "qty": 0.5, "valid_until": (NOW + timedelta(minutes=5)).isoformat(), "status": "emitted"})
    monkeypatch.setattr(ptr, "plan_amends", lambda *a, **k: {})
    assert pe.claim_next(_dev(), now=NOW, accepts=("amend",))["ticket_id"] == "E1"
    assert pe.claim_next(_dev(), now=NOW, accepts=("amend",))["kind"] == "amend"


def test_submit_follows_the_entry_decision(monkeypatch):
    _parent()
    ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: UP)
    monkeypatch.setenv("PROP_PHONE_MODE_BREAKOUT_2", "dry")
    assert ptr.claim_amend(_dev(), now=NOW)["submit"] == "dry"


def _claimed():
    _parent()
    ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: UP)
    return ptr.claim_amend(_dev(), now=NOW)


def test_amended_moves_resting_sl_and_next_plan_starts_from_it(_iso):
    a = _claimed()
    r = pe.record_report(_dev(), {"kind": "amend_result", "ticket_id": "T1", "amend_id": a["amend_id"],
                                  "result": "amended", "sl_read": 2530.0, "tp_read": 2800.0})
    assert r["ok"] and r["result"] == "amended"
    tr = _trail()
    assert tr["resting_sl"] == 2530.0 and tr["seen"] and not tr.get("locked")
    assert any("READ BACK" in m for m in _iso)
    later = _candles([2520, 2560, 2600, 2650], last_close=2640)
    ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=1), candles_fn=lambda s, tf: later)
    am = _trail()["amend"]
    assert am["id"] == "T1#a2" and am["from_sl"] == 2530.0 and am["sl"] == pytest.approx(2580.0)


def _report(a, result, **kw):
    return pe.record_report(_dev(), {"kind": "amend_result", "ticket_id": "T1", "amend_id": a["amend_id"],
                                     "result": result, **kw})


def _rising(n):
    """n+3 hourly bars whose highs keep rising, so every bar plans a tighter stop."""
    return _candles([2520 + 40 * i for i in range(n + 3)])


def _attempt(hours, result="refused", **kw):
    """Plan + claim at NOW+hours on a still-rising feed, then report ``result``. Returns the claim or None."""
    t = NOW + timedelta(hours=hours)
    ptr.plan_amends("breakout_2", now=t, candles_fn=lambda s, tf: _rising(hours))
    a = ptr.claim_amend(_dev(), now=t)
    if a is not None:
        _report(a, result, **kw)
    return a


def test_amended_claim_that_does_not_match_the_ask_is_a_mismatch_and_retries(_iso):
    a = _claimed()
    r = _report(a, "amended", sl_read=2520.0, tp_read=2800.0)
    assert r["result"] == "mismatch"
    tr = _trail()
    assert "locked" not in tr and not tr.get("human_hold") and tr["fails"] == 1
    assert sum("NOT verified" in m for m in _iso) == 1
    # the terminal's read-back is the server's belief now, so the retry's pre-click check matches the terminal
    assert tr["resting_sl"] == 2520.0
    b = _attempt(1, "amended", sl_read=2570.0, tp_read=2800.0)
    assert b["amend_id"] == "T1#a2" and b["from_sl"] == 2520.0  # retried, never a standing block
    assert _trail()["fails"] == 0 and _trail()["resting_sl"] == 2570.0


def test_refused_amend_does_not_stop_the_next_attempt(_iso):
    a = _claimed()
    _report(a, "refused", reason="edit control not found")
    assert _trail()["fails"] == 1 and "locked" not in _trail()
    b = _attempt(1)
    assert b is not None and b["amend_id"] == "T1#a2"
    assert sum("refused before any click" in m for m in _iso) == 1  # first of the streak only


def test_backoff_then_retry():
    a = _claimed()
    _report(a, "refused", reason="x")                      # fail 1 -> wait 1 bar
    assert _attempt(1) is not None                         # fail 2 -> wait 2 bars (from NOW+1h)
    assert _attempt(2) is None                             # inside the backoff: no plan, no claim
    assert _trail()["amend"]["id"] == "T1#a2"
    assert _attempt(3) is not None                         # wait passed: retried (fail 3 -> wait 4 bars)
    assert _attempt(5) is None and _attempt(6) is None
    assert _attempt(7)["amend_id"] == "T1#a4"              # fail 4 -> wait 8 bars (cap)
    assert ptr._backoff_bars(4) == ptr._backoff_bars(50) == ptr.BACKOFF_CAP_BARS


def test_repeated_failures_raise_one_red_flag_and_keep_trying_then_recover(_iso):
    a = _claimed()
    _report(a, "refused", reason="x")
    for h in (1, 3, 7, 15, 23):
        assert _attempt(h, "mismatch" if h == 3 else "refused", reason="x") is not None
    tr = _trail()
    assert tr["fails"] == 6 and tr["flagged"] and "locked" not in tr
    assert sum(m.startswith("📱 trail breakout_2: 🚩") for m in _iso) == 1   # exactly ONE red flag
    assert len(_iso) == 2                                                   # first failure + the flag
    assert _attempt(31, "amended", sl_read=2520.0 + 40 * 33 - 70, tp_read=2800.0) is not None
    assert sum("RECOVERED" in m for m in _iso) == 1 and not _trail().get("flagged") and _trail()["fails"] == 0


def test_human_moved_still_defers_until_the_human_stop_is_on_record(_iso):
    a = _claimed()
    _report(a, "human_moved", reason="terminal SL 2470 != 2450")
    assert "human" in _trail()["human_hold"]
    later = _candles([2520, 2560, 2600, 2700], last_close=2690)
    for h in (1, 5, 30):  # the trail never fights a human's stop, whatever the price does
        ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=h), candles_fn=lambda s, tf: later)
        assert ptr.claim_amend(_dev(), now=NOW + timedelta(hours=h)) is None
    assert ptr.request_tp_amend("breakout_2", "T1", 2900.0, reason="x", now=NOW)["ok"] is False
    assert sum("will not override a human" in m for m in _iso) == 1
    # the human {kind: amend} report puts the human's stop on record and the trail resumes from it
    assert ptr.note_human_amend("breakout_2", "T1", sl=2470.0)
    assert not _trail().get("human_hold") and _trail()["resting_sl"] == 2470.0
    ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=31), candles_fn=lambda s, tf: later)
    assert ptr.claim_amend(_dev(), now=NOW + timedelta(hours=31))["from_sl"] == 2470.0


def test_stop_is_never_loosened_after_failures():
    a = _claimed()
    _report(a, "amended", sl_read=2530.0, tp_read=2800.0)
    b = _attempt(1, "refused", reason="x")
    assert b["sl"] > 2530.0
    # price falls back: the replay would sit below the resting stop -> no amend, the stop stays where it is
    ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=3), candles_fn=lambda s, tf: _candles([2505, 2508, 2510]))
    am = _trail()["amend"]
    assert am["id"] == b["amend_id"] and _trail()["resting_sl"] == 2530.0


def test_claimed_amend_with_no_report_is_a_failed_attempt_and_retries(_iso):
    _claimed()
    ptr.plan_amends("breakout_2", now=NOW + timedelta(seconds=ptr.CLAIM_TIMEOUT_S + 5), candles_fn=lambda s, tf: UP)
    tr = _trail()
    assert tr["amend"]["status"] == "unreported" and tr["fails"] == 1 and "locked" not in tr
    assert sum("never reported" in m for m in _iso) == 1
    assert _attempt(1) is not None  # the next attempt re-reads the terminal before any click


def test_stale_lock_from_the_old_code_is_ignored(caplog):
    for tid, lock in (("T1", "2 refusals: edit control not found"), ("T2", "amend clicked but not verified: x")):
        _parent(tid, meta={ptr.KEY: {"locked": lock}})
    _parent("T3", meta={ptr.KEY: {"ended": "position never seen on the terminal in 6 reads"}})
    _parent("T4", meta={ptr.KEY: {"locked": "the terminal's stop differs from the server's: a human moved it"}})
    with caplog.at_level("INFO", logger="src.prop.phone_trail"):
        out = ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: UP)
    assert sorted(out["emitted"]) == ["T1#a1", "T2#a1", "T3#a1"]
    assert sum("ignoring stale" in r.getMessage() for r in caplog.records) == 3
    assert "locked" not in _trail("T1") and "ended" not in _trail("T3")
    # an old human-moved lock is carried over as the human hold: a human's stop stays held
    assert "human" in ptr._load_trail({ptr.KEY: _trail("T4")}, "T4")["human_hold"] and not _trail("T4").get("amend")


def test_no_position_never_ends_the_trail(_iso):
    a = _claimed()
    _report(a, "no_position")
    tr = _trail()
    assert tr["no_position"] == 1 and "ended" not in tr
    assert sum("no matching row in Positions" in m for m in _iso) == 1
    assert _attempt(1, "no_position") is not None          # a transient miss is re-checked


def test_seen_position_gone_is_rechecked_not_ended(_iso):
    a = _claimed()
    _report(a, "amended", sl_read=2530.0, tp_read=2800.0)
    b = _attempt(1, "no_position")
    assert b is not None and "ended" not in _trail() and _trail()["absent"] == 1
    assert _attempt(2, "amended", sl_read=2610.0, tp_read=2800.0) is not None  # it was a transient read
    assert _trail()["absent"] == 0 and not any("🚩" in m for m in _iso)


def test_stale_or_foreign_result_is_refused():
    a = _claimed()
    r = pe.record_report(_dev(), {"kind": "amend_result", "ticket_id": "T1", "amend_id": "T1#a9",
                                  "result": "amended", "sl_read": 2530.0})
    assert r["ok"] is False
    with pytest.raises(ValueError):
        pe.record_report(_dev(), {"kind": "amend_result", "ticket_id": "T1", "amend_id": a["amend_id"],
                                  "result": "bogus"})


def test_dry_amended_keeps_resting_levels_and_pings_once(_iso):
    a = _claimed()
    pe.record_report(_dev(), {"kind": "amend_result", "ticket_id": "T1", "amend_id": a["amend_id"],
                              "result": "dry_amended", "sl_read": 2530.0, "tp_read": 2800.0})
    tr = _trail()
    assert tr.get("resting_sl") is None and tr["amend"]["status"] == "dry_amended"
    assert sum("DRY amend" in m for m in _iso) == 1


def test_tp_amend_through_the_same_slot():
    _parent()
    r = ptr.request_tp_amend("breakout_2", "T1", 2900.0, reason="tp revision: thesis intact", now=NOW)
    assert r["ok"]
    am = _trail()["amend"]
    assert am["kind"] == "tp" and am["sl"] == 2450.0 and am["tp"] == 2900.0
    assert ptr.request_tp_amend("breakout_2", "T1", 2950.0, reason="x", now=NOW)["ok"] is False  # outstanding
    assert ptr.request_tp_amend("breakout_2", "T1", 2400.0, reason="x", now=NOW + timedelta(hours=1))["ok"] is False


def test_pending_counts_amends_only_for_an_accepting_app(monkeypatch):
    _parent()
    ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: UP)
    monkeypatch.setattr(ptr, "plan_amends", lambda *a, **k: {})
    assert pe.pending_count(_dev(), now=NOW) == 0
    assert pe.pending_count(_dev(), now=NOW, accepts=("amend",)) == 1


def test_claim_route_accepts_capability(monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.api.main import app
    _parent()
    assert ptr.request_tp_amend("breakout_2", "T1", 2900.0, reason="route test")["ok"]  # emitted, real clock
    monkeypatch.setattr(ptr, "plan_amends", lambda *a, **k: {})
    c = TestClient(app)
    h = {"Authorization": "Bearer " + TOKEN}
    assert c.post("/api/bot/prop/phone/claim", headers=h).json()["ticket"] is None     # old app, no body
    assert c.get("/api/bot/prop/phone/pending", headers=h).json()["pending"] == 0
    assert c.get("/api/bot/prop/phone/pending?accepts=amend", headers=h).json()["pending"] == 1
    got = c.post("/api/bot/prop/phone/claim", headers=h, json={"accepts": ["amend"]}).json()["ticket"]
    assert got["kind"] == "amend" and got["ticket_id"] == "T1" and got["tp"] == 2900.0
