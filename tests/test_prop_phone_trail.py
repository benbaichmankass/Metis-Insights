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
    # the live pencil + Confirm gate (3 drill passes or the operator's pin) is OPEN for the amend-path tests; the
    # closed-gate behaviour has its own tests below
    monkeypatch.setattr(ptr, "_sheet_edit_gate", lambda acct: {"enabled": True, "source": "config", "passes": 0})
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
    _report(a, "refused", reason="edit control not found", capture_run="T1-a1-20261018T120000Z")
    assert _trail()["fails"] == 1 and "locked" not in _trail()
    # a LIVE refusal (PHONE-EXEC-OBSERVABLE-1018 piece 5): EVERY one names the stop to set by hand + the captures
    assert len(_iso) == 1 and "SET THE STOP BY HAND" in _iso[0] and "ETHUSDT long stop -> 2530.0 (from 2450.0)" in _iso[0]
    assert "/api/diag/phone_capture?account_id=breakout_2&run_id=T1-a1-20261018T120000Z" in _iso[0]
    assert _trail()["amend"]["capture_run"] == "T1-a1-20261018T120000Z"
    b = _attempt(1, capture_run="bad run id!")
    assert b is not None and b["amend_id"] == "T1#a2"
    assert sum("refused before any click" in m.lower() for m in _iso) == 2, "a live refusal is never silent, streak or not"
    assert "Step captures: none posted" in _iso[1] and "capture_run" not in _trail()["amend"]


def test_dry_refusal_pings_once_per_streak_without_a_hand_set_ask(_iso, monkeypatch):
    monkeypatch.setenv("PROP_PHONE_MODE_BREAKOUT_2", "dry")
    a = _claimed()
    _report(a, "refused", reason="edit control not found")
    assert _attempt(1) is not None
    assert sum("refused before any click" in m for m in _iso) == 1  # first of the streak only
    assert not any("SET THE STOP BY HAND" in m for m in _iso), "nothing to set by hand when no live amend was due"


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
    # every LIVE refusal names the stop to set by hand (5 of the 6 failures are refusals; the mismatch is not the
    # first of its streak) + the one flag
    assert len(_iso) == 6 and sum('SET THE STOP BY HAND' in m for m in _iso) == 5
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


def test_tickets_api_view_exposes_the_trail_target(monkeypatch):
    # PI-20261015-JWMGXOTH-0001: the trail's asked stop lived only in prop_tickets.meta.phone_trail, which no session
    # read returned. list_outbound_tickets (/api/bot/prop/tickets) now projects it; absent fields stay None, never 0.
    monkeypatch.setattr(prop_journal, "_prop_scope", lambda acct: set())   # sidecar-only view: no order_packages
    _parent("T1")
    _parent("T2", status="placed")
    ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: UP)
    rows = {r["ticket_id"]: r for r in prop_journal.list_outbound_tickets(account_id="breakout_2")}
    pt = rows["T1"]["phone_trail"]
    assert pt["amend_status"] == "emitted" and pt["amend_kind"] == "sl" and pt["amend_id"] == "T1#a1"
    assert pt["amend_sl"] == pytest.approx(2530.0) and pt["amend_from_sl"] == 2450.0
    assert pt["replay_sl"] == pytest.approx(2530.0) and pt["last_plan_at"]
    assert pt["resting_sl"] is None or isinstance(pt["resting_sl"], float)
    assert pt["human_hold"] is None
    prop_journal.record_ticket({"ticket_id": "T3", "account_id": "breakout_2", "strategy": "trend_donchian_eth_prop",
                                "symbol": "ETHUSDT", "direction": "long", "entry": 1.0, "sl": 0.9, "tp": 1.2, "status": "suppressed"})
    rows = {r["ticket_id"]: r for r in prop_journal.list_outbound_tickets(account_id="breakout_2")}
    assert rows["T3"]["phone_trail"] is None


def test_phone_trail_projection_never_invents_zero():
    assert prop_journal._phone_trail_meta(None) is None
    assert prop_journal._phone_trail_meta("not json") is None
    pt = prop_journal._phone_trail_meta({"phone_trail": {"amend": {"sl": "x"}, "last_plan": {}}})
    assert pt["amend_sl"] is None and pt["replay_sl"] is None and pt["resting_sl"] is None and pt["amend_status"] is None


# ── trail crossed (PI-20261015-JWMGXOTH-0002) ────────────────────────────────
# short: entry 2500, sl 2550 -> atr 20, trail = ext + 70. Bar lows 2400 then 2475: replay 2470; last close 2485
CROSSED_SHORT = _candles([2415, 2490])


def test_short_trail_crossed_emits_a_tighten_only_exit_amend_and_alerts_once(_iso):
    _parent(direction="short")
    out = ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: CROSSED_SHORT)
    am = _trail()["amend"]
    assert out["emitted"] == ["T1#a1"] and am["kind"] == "sl" and am["from_sl"] == 2550.0
    # 2485 + max(2 steps, 0.001 x 2485 = 2.485, 0.1 x 20 = 2.0) = 2487.485 -> rounded up to the step
    assert 2487.48 < am["sl"] <= 2487.5 and am["sl"] < 2550.0
    assert am["reason"].startswith("trail crossed")
    assert _trail()["last_plan"]["action"] == "exit"
    assert sum("trail CROSSED" in x for x in out["alerts"]) == 1


def test_long_trail_crossed_exit_amend_sits_under_the_price():
    _parent(direction="long")
    c = _candles([2600, 2520])          # ext 2600 -> trail 2530; last close 2515 is under it
    out = ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: c)
    am = _trail()["amend"]
    assert out["emitted"] == ["T1#a1"] and am["from_sl"] == 2450.0
    assert 2512.4 <= am["sl"] < 2512.52 and am["sl"] > 2450.0          # 2515 - 2.515, floored


def test_trail_crossed_no_amend_when_the_resting_stop_is_already_tighter():
    _parent(direction="short", meta={"phone_trail": {"resting_sl": 2486.0}})
    out = ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: CROSSED_SHORT)
    tr = _trail()
    assert out["emitted"] == [] and "amend" not in tr
    assert tr["last_plan"]["action"] == "exit" and "already at or inside" in tr["last_plan"]["why"]


# ---------------------------------------------------------------------------------------------------------------
# An UNREAD stop is never a human move (operator 2026-10-18, breakout_2 prop-manual-c1dba3c376b3#a7: the app
# reported human_moved with "terminal stop null != expected 2452.65" and the server held a trail nobody had touched).
# ---------------------------------------------------------------------------------------------------------------
_UNREAD_FORM = {"ok": True, "n_stop": 1, "n_take": 1, "rows": [{"type": "stop", "trigger": None, "n_px": 2},
                                                              {"type": "take", "trigger": 2800.0, "n_px": 1}]}


def test_unread_stop_report_is_a_failed_attempt_not_a_human_move(_iso):
    a = _claimed()
    r = _report(a, "human_moved", reason="terminal stop null != expected 2450.0 (sheet close: escape)",
                form=_UNREAD_FORM)
    assert r["ok"]
    tr = _trail()
    assert not tr.get("human_hold"), "an unread stop must never hold the trail"
    assert tr["amend"]["status"] == ptr.UNREAD and tr["amend"]["read_state"] == "unread"
    assert tr["fails"] == 1 and tr["backoff"]["n"] == 1
    assert not any("will not override a human" in m for m in _iso)
    live = [m for m in _iso if "did NOT READ" in m]
    assert len(live) == 1 and "n=2" in live[0] and "2530.0" in live[0], live
    # the next plan re-emits from the server's resting stop once the backoff has passed: nothing is held
    later = _candles([2520, 2560, 2600, 2640])
    ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=2), candles_fn=lambda s, tf: later)
    c = ptr.claim_amend(_dev(), now=NOW + timedelta(hours=2))
    assert c is not None and c["from_sl"] == 2450.0


def test_unread_is_decided_by_the_evidence_not_the_word_human(_iso):
    # no dump, reason text says the reading was null -> unread
    assert ptr._unread_stop("terminal stop null != expected 2452.65 (sheet close: escape)", None, {}) is not None
    assert ptr._unread_stop("modal trigger null != expected 2452.65", None, None) is not None
    # a dump whose Stop row carries no trigger -> unread, whatever the text says
    assert "n=2" in ptr._unread_stop("terminal differs", None, _UNREAD_FORM)
    assert "no Stop row" in ptr._unread_stop("terminal differs", None, {"ok": True, "rows": []})
    # two numbers, or a value the app read back -> a genuine human move, never reclassified
    assert ptr._unread_stop("terminal SL 2470 != 2450", None, {}) is None
    assert ptr._unread_stop("terminal stop 2470.0 != expected 2450.0", None, {}) is None
    assert ptr._unread_stop("terminal stop null != expected 2450.0", 2470.0, {}) is None
    assert ptr._unread_stop("terminal differs", None,
                            {"rows": [{"type": "stop", "trigger": 2470.0, "n_px": 1}]}) is None


def test_a_genuine_human_move_still_holds(_iso):
    a = _claimed()
    _report(a, "human_moved", reason="terminal stop 2470.0 != expected 2450.0 (sheet close: escape)",
            form={"ok": True, "n_stop": 1, "rows": [{"type": "stop", "trigger": 2470.0, "n_px": 1}]})
    assert "human" in _trail()["human_hold"]
    assert sum("will not override a human" in m for m in _iso) == 1


def _held_meta(reason: str, *, trigger=None, n_px=2):
    return {ptr.KEY: {
        "seq": 1, "last_bar": "2026-10-07T13:00:00+00:00",
        "amend": {"id": "T1#a1", "status": "human_moved", "kind": "sl", "from_sl": 2450.0, "sl": 2530.0,
                  "emitted_at": NOW.isoformat(), "reported_at": NOW.isoformat(), "reason_reported": reason,
                  "sl_read": None, "tp_read": None,
                  "form": {"ok": True, "n_stop": 1, "rows": [{"type": "stop", "trigger": trigger, "n_px": n_px}]}},
        "human_hold": "the terminal's stop differs from the server's: a human moved it", "fails": 6}}


def test_hold_written_from_an_unread_report_is_released_on_the_next_plan(_iso):
    """The breakout_2 shape on main at 13:01Z: held by an older app's null reading. The next plan releases the
    hold through a code path (a hold_released history row), re-plans from the server's stop, and emits."""
    _parent(meta=_held_meta("terminal stop null != expected 2450.0 (sheet close: escape)"))
    later = _candles([2520, 2560, 2600, 2640])
    out = ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=1), candles_fn=lambda s, tf: later)
    tr = _trail()
    assert not tr.get("human_hold")
    assert tr["history"][-1]["status"] == "hold_released" and "unread stop" in tr["history"][-1]["why"]
    assert any("hold RELEASED" in a for a in out["alerts"])
    assert out["emitted"] == ["T1#a2"] and tr["amend"]["from_sl"] == 2450.0
    assert ptr.claim_amend(_dev(), now=NOW + timedelta(hours=1))["amend_id"] == "T1#a2"


def test_hold_from_a_genuine_human_move_is_never_released_by_the_planner(_iso):
    _parent(meta=_held_meta("terminal stop 2470.0 != expected 2450.0 (sheet close: escape)", trigger=2470.0, n_px=1))
    later = _candles([2520, 2560, 2600, 2640])
    out = ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=1), candles_fn=lambda s, tf: later)
    assert "human" in _trail()["human_hold"] and out["emitted"] == []
    assert ptr.claim_amend(_dev(), now=NOW + timedelta(hours=1)) is None


# ---------------------------------------------------------------------------------------------------------------
# A NEW signed exec.js waives a running backoff ONCE (operator 2026-10-18: breakout_2 #a8 sat at fails=7 = an 8-bar
# wait while the reader fix shipped signed; the fix is tried on the next plan, not tomorrow).
# ---------------------------------------------------------------------------------------------------------------
def test_new_served_exec_js_waives_the_backoff_once(_iso, monkeypatch):
    monkeypatch.setattr(ptr, "_served_exec_js", lambda: "2026-10-18.3")
    a = _claimed()
    _report(a, "refused", reason="sheet volume 0.5 != ticket qty 0.52")
    assert _trail()["backoff"] == {"n": 1, "since": a["claimed_at"], "exec_js": "2026-10-18.3"}
    # a second failure one bar later: 2 closed bars of backoff (until NOW+3h), recorded against the same script
    b = _attempt(2, "refused", reason="sheet volume 0.5 != ticket qty 0.52")
    assert b is not None and _trail()["fails"] == 2 and _trail()["backoff"]["exec_js"] == "2026-10-18.3"
    later = _rising(6)
    # same script, next closed bar, still inside the 2-bar wait (until NOW+4h): nothing emitted
    out = ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=3, minutes=5), candles_fn=lambda s, tf: later)
    assert out["emitted"] == []
    # a NEWER served script: the wait is waived once, the plan emits from the server's resting stop
    monkeypatch.setattr(ptr, "_served_exec_js", lambda: "2026-10-18.4")
    out = ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=3, minutes=6), candles_fn=lambda s, tf: later)
    assert out["emitted"] == ["T1#a3"], out["log"]
    assert any("backoff waived once" in x.get("why", "") for x in out["log"])
    assert _trail()["backoff"]["exec_js"] == "2026-10-18.4"
    # ... and only once per version: a failure on the new script backs off normally (4 bars now)
    c = ptr.claim_amend(_dev(), now=NOW + timedelta(hours=3, minutes=6))
    _report(c, "refused", reason="sheet volume 0.5 != ticket qty 0.52")
    assert _trail()["fails"] == 3
    out = ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=4, minutes=5), candles_fn=lambda s, tf: later)
    assert out["emitted"] == []


def test_backoff_without_a_served_script_keeps_its_clock(_iso, monkeypatch):
    monkeypatch.setattr(ptr, "_served_exec_js", lambda: None)
    a = _claimed()
    _report(a, "refused", reason="x")
    assert _trail()["backoff"]["exec_js"] is None
    later = _candles([2520, 2560, 2600, 2640])
    assert ptr.plan_amends("breakout_2", now=NOW + timedelta(minutes=30), candles_fn=lambda s, tf: later)["emitted"] == []


# ---------------------------------------------------------------------------------------------------------------
# THE GATE: while sheet_edit is closed (drill passes < 3, no pin) the server emits NO amend — the app would refuse the
# pencil by design and every refusal burned the backoff (measured 2026-10-18). One alert per ticket; nothing backs off;
# the first plan after the gate opens emits at once, and the exec.js waiver is not consumed by a gated plan.
# ---------------------------------------------------------------------------------------------------------------
def test_closed_sheet_edit_gate_emits_nothing_and_alerts_once(_iso, monkeypatch):
    monkeypatch.setattr(ptr, "_sheet_edit_gate", lambda acct: {"enabled": False, "source": "drills", "passes": 1, "needed": 3})
    _parent()
    out = ptr.plan_amends("breakout_2", now=NOW, candles_fn=lambda s, tf: UP)
    assert out["emitted"] == [] and any("gate closed (drills: passes 1/3)" in x["why"] for x in out["log"])
    assert sum("no amend emitted while the sheet_edit gate closed" in m for m in _iso) == 1
    tr = _trail()
    assert tr.get("gate_alerted") and "backoff" not in tr and "last_bar" not in tr and "amend" not in tr
    # a second gated plan: logged, not alerted again
    out = ptr.plan_amends("breakout_2", now=NOW + timedelta(minutes=5), candles_fn=lambda s, tf: UP)
    assert out["emitted"] == [] and sum("no amend emitted" in m for m in _iso) == 1
    # the gate opens (3 passes): the very next plan emits from the server's resting stop
    monkeypatch.setattr(ptr, "_sheet_edit_gate", lambda acct: {"enabled": True, "source": "drills", "passes": 3, "needed": 3})
    out = ptr.plan_amends("breakout_2", now=NOW + timedelta(minutes=6), candles_fn=lambda s, tf: UP)
    assert out["emitted"] == ["T1#a1"]
    assert ptr.claim_amend(_dev(), now=NOW + timedelta(minutes=6))["from_sl"] == 2450.0


def test_gated_plans_do_not_consume_the_exec_js_waiver(_iso, monkeypatch):
    monkeypatch.setattr(ptr, "_served_exec_js", lambda: "2026-10-18.3")
    a = _claimed()
    _report(a, "refused", reason="sheet read verified; the pencil click waits for the server's sheet_edit")
    b = _attempt(2, "refused", reason="sheet read verified; the pencil click waits for the server's sheet_edit")
    assert b is not None and _trail()["backoff"]["n"] == 2   # 2 closed bars: until NOW+4h
    later = _rising(6)
    # the gate closes; a newer script is served; gated plans return before the backoff check (waiver untouched)
    monkeypatch.setattr(ptr, "_sheet_edit_gate", lambda acct: {"enabled": False, "source": "drills", "passes": 2, "needed": 3})
    monkeypatch.setattr(ptr, "_served_exec_js", lambda: "2026-10-18.4")
    out = ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=2, minutes=10), candles_fn=lambda s, tf: later)
    assert out["emitted"] == [] and _trail()["backoff"]["exec_js"] == "2026-10-18.3"
    # the gate opens inside the wait: the waiver fires now and the amend emits
    monkeypatch.setattr(ptr, "_sheet_edit_gate", lambda acct: {"enabled": True, "source": "drills", "passes": 3, "needed": 3})
    out = ptr.plan_amends("breakout_2", now=NOW + timedelta(hours=3, minutes=5), candles_fn=lambda s, tf: later)
    assert out["emitted"] == ["T1#a3"] and _trail()["backoff"]["exec_js"] == "2026-10-18.4"
