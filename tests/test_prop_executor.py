"""Prop executor, step 3 (PROP-EXEC 2026-09-28).

Spec: docs/research/prop-automation-options-2026-09-27.md § 3.2–3.5, § 4 step 3.

What these prove: every § 3.3 guard refuses on its own condition and on
"could not look"; every § 3.5 failure row is contained as the table says;
the ledger row is written BEFORE the click; ``read_only`` clicks nothing and
writes nothing; the dry run walks a ticket to a verified form and stops; the
dxtrade order controls work against an INVENTED ticket form in a real
Chromium and never press submit unless armed.

What they do NOT prove: that the real Breakout order ticket looks like the
invented one. The ``probe-ticket`` system-action is the measurement.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.prop import prop_executor as pe
from src.prop.platform.base import AccountSnapshot, BracketSpec, PlaceAttempt, Position, WorkingOrder
from src.prop.platform.dxtrade import (
    DXtradeAdapter,
    check_bracket_spec,
    classify_ticket_surface,
    form_names_symbol,
    verify_form_selection,
    verify_form_values,
)

NOW = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


def cfg(**kw) -> pe.ExecutorConfig:
    base = dict(
        account_size_usd=5000.0, daily_loss_pct=0.03, max_dd_pct=0.06, safety_margin_usd=5.0,
        risk_cap_usd=75.0, unconfirmed_reads=3,
        symbols={"SOLUSDT": {"venue": "SOLUSD", "cvpp": 1.0, "lot_units": 1.0, "min_lots": 0.01, "lot_step": 0.01}},
    )
    base.update(kw)
    return pe.ExecutorConfig(**base)


def ticket(**kw):
    t = {"ticket_id": "prop-manual-aaa", "symbol": "SOLUSDT", "direction": "long", "entry": 120.0,
         "sl": 118.0, "tp": 126.0, "qty": 0.5, "risk_usd": 1.0,
         "valid_until": (NOW + timedelta(minutes=30)).isoformat(), "created_at": NOW.isoformat()}
    t.update(kw)
    return t


def acct(balance=5000.0, equity=5000.0, realized_today=0.0):
    return AccountSnapshot(balance=balance, equity=equity, unrealized=0.0, realized_today=realized_today)


def guards(t=None, account=None, ds=5000.0, orisk=0.0, ostate="no_open_positions", c=None, halted=None,
           max_lots=None):
    c = c or cfg()
    t = t or ticket()
    spec, facts, refusal = pe.bracket_from_ticket(t, c, max_lots=max_lots)
    return pe.evaluate_guards(ticket=t, spec=spec, facts=facts, refusal=refusal, account=account or acct(),
                              day_start_balance=ds, open_risk_usd=orisk, open_risk_state=ostate,
                              cfg=c, now=NOW, halted=halted)


# ── kill switch ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,want", [(None, "read_only"), ("", "read_only"), ("LIVE", "live"),
                                      ("off", "off"), ("liev", "read_only"), ("read_only", "read_only")])
def test_mode_defaults_to_read_only_and_a_typo_never_arms_live(raw, want):
    env = {} if raw is None else {pe.MODE_ENV: raw}
    assert pe.executor_mode(env) == want


def test_tick_modes():
    from scripts.prop.prop_executor_tick import resolve_mode
    ns = lambda **k: SimpleNamespace(**{"probe_ticket": "", "dry_run": False, "watched_click": False, **k})  # noqa: E731
    assert resolve_mode(ns(), {}) == "read_only"
    assert resolve_mode(ns(dry_run=True), {pe.MODE_ENV: "live"}) == "read_only"
    assert resolve_mode(ns(watched_click=True), {}) == "not_armed"          # must be armed explicitly
    assert resolve_mode(ns(watched_click=True), {pe.MODE_ENV: "live"}) == "live"
    assert resolve_mode(ns(probe_ticket="SOLUSD"), {}) == "probe"


def test_real_config_loads_and_keeps_flat_75_and_unmeasured_lots_refuse():
    c = pe.load_config("breakout_1")
    assert c.risk_cap_usd == 75.0 and c.max_dd_pct == 0.06 and c.daily_loss_pct == 0.03
    assert c.symbols["BTCUSDT"]["lot_units"] is None      # UNMEASURED → refused
    assert c.symbols["ADAUSDT"]["min_lots"] == 10
    spec, _, why = pe.bracket_from_ticket(ticket(symbol="BTCUSDT"), c)
    assert spec is None and "not declared" in why
    assert c.watched_click_max_lots == {"SOLUSD": 0.01}   # the lot step; only SOLUSD (#13855)


# ── § 3.3 guards, one at a time ───────────────────────────────────────────


def test_a_ticket_with_room_fits():
    v = guards()
    assert v.fits, v.reasons
    assert v.checks["ticket_risk_usd"] == 1.0  # 0.5 lots x 1 unit x |120-118| x 1.0


def test_static_dd_refuses_at_the_measured_24_dollar_cushion():
    # Balance ~$4,724 on 2026-09-27: floor $4,700, cushion $24. A flat-$75
    # ticket (37.5 SOL at a $2 stop) must be refused — correct behaviour.
    v = guards(t=ticket(qty=37.5), account=acct(4724.0, 4724.0), ds=4724.0)
    assert not v.fits
    assert any(r.startswith("static DD") for r in v.reasons)
    assert any(r.startswith("risk gate: exceeds_cushion") for r in v.reasons)


def test_static_dd_margin_is_applied():
    # equity 4725, risk 20 → after 4705 <= 4700 + 5
    v = guards(t=ticket(qty=10.0), account=acct(4725.0, 4725.0), ds=4725.0)
    assert any(r.startswith("static DD") for r in v.reasons)


def test_daily_loss_refuses():
    # day start 5200 → floor 5044; equity 5060 - risk 20 = 5040
    v = guards(t=ticket(qty=10.0), account=acct(5060.0, 5060.0), ds=5200.0)
    assert any(r.startswith("daily loss") for r in v.reasons)


def test_daily_loss_unknown_day_start_refuses():
    v = guards(ds=None)
    assert any("day-start balance unknown" in r for r in v.reasons)


def test_open_risk_counts_against_the_cushion():
    # 4760 - 55 open - 2 ticket = 4703 <= 4700 + 5 margin → refused
    v = guards(t=ticket(qty=1.0), account=acct(4760.0, 4760.0), ds=4760.0, orisk=55.0, ostate="measured")
    assert any(r.startswith("static DD") for r in v.reasons)
    v2 = guards(t=ticket(qty=1.0), account=acct(4760.0, 4760.0), ds=4760.0, orisk=0.0)
    assert v2.fits


@pytest.mark.parametrize("state", ["stop_unknown", "unrealized_unreported", "unreadable"])
def test_open_risk_could_not_look_refuses(state):
    v = guards(orisk=None, ostate=state)
    assert not v.fits and any(state in r for r in v.reasons)


def test_account_not_read_refuses():
    v = guards(account=AccountSnapshot(balance=None, equity=None))
    assert not v.fits and any("could not look" in r for r in v.reasons)


def test_risk_gate_enforce_cap_refuses_above_flat_75():
    v = guards(t=ticket(qty=40.0), account=acct(9000.0, 9000.0), ds=9000.0)
    assert any(r.startswith("risk cap: resized") for r in v.reasons)


def test_risk_is_recomputed_from_lots_not_the_tickets_claim():
    v = guards(t=ticket(qty=40.0, risk_usd=1.0), account=acct(9000.0, 9000.0), ds=9000.0)
    assert v.checks["ticket_risk_usd"] == 80.0


@pytest.mark.parametrize("kw,needle", [
    ({"sl": None}, "lacks SL or TP"), ({"tp": None}, "lacks SL or TP"),
    ({"symbol": "DOGEUSDT"}, "not in breakout_routing"), ({"qty": 0.001}, "rounds to zero"),
    ({"direction": None}, "no long/short"), ({"tp": 119.0}, "sl < entry < tp"),
])
def test_structure_refuses(kw, needle):
    v = guards(t=ticket(**kw))
    assert not v.fits and any(needle in r for r in v.reasons), v.reasons


def test_below_venue_minimum_refuses():
    c = cfg(symbols={"SOLUSDT": {"venue": "SOLUSD", "cvpp": 1.0, "lot_units": 1.0, "min_lots": 10, "lot_step": 1}})
    v = guards(t=ticket(qty=5), c=c)
    assert any("below the venue minimum" in r for r in v.reasons)


def test_size_is_rounded_down_never_up():
    lots, _ = pe.size_lots(0.5699, {"venue": "X", "lot_units": 1, "lot_step": 0.01, "min_lots": 0.01})
    assert lots == 0.56


def test_expired_and_missing_validity_refuse():
    assert any("expired" in r for r in guards(t=ticket(valid_until=(NOW - timedelta(seconds=1)).isoformat())).reasons)
    assert any("no readable valid_until" in r for r in guards(t=ticket(valid_until=None)).reasons)


def test_halt_refuses():
    assert any("halted" in r for r in guards(halted="orphan").reasons)


def test_watched_click_min_size_can_fit_where_the_flat_ticket_cannot():
    small = guards(t=ticket(qty=37.5), account=acct(4724.0, 4724.0), ds=4724.0, max_lots=0.5)
    assert small.fits and small.checks["ticket_risk_usd"] == 1.0


# ── day start, open risk, confirmation (pure) ─────────────────────────────


def test_trading_day_rolls_at_0030_utc():
    assert pe.trading_day(datetime(2026, 9, 30, 0, 29, tzinfo=timezone.utc)) == "2026-09-29"
    assert pe.trading_day(datetime(2026, 9, 30, 0, 30, tzinfo=timezone.utc)) == "2026-09-30"


def test_day_start_takes_the_higher_candidate():
    st = {}
    assert pe.day_start_balance(st, acct(4700.0, 4700.0, realized_today=-50.0), NOW, "00:30") == 4750.0
    st2 = {}
    assert pe.day_start_balance(st2, AccountSnapshot(balance=None), NOW, "00:30") is None


def test_open_risk_positions_and_orders():
    c = cfg()
    p = Position(symbol="SOLUSD", side="long", quantity=1.0, entry_price=120.0, stop_loss=118.0,
                 take_profit=126.0, unrealized_pnl=3.0)
    o = WorkingOrder(symbol="SOLUSD", side="long", quantity=1.0, price=119.0, stop_loss=117.0)
    assert pe.open_risk([p], [o], c) == (7.0, "measured")  # (3 - -2) + 2
    assert pe.open_risk([], [], c) == (0.0, "no_open_positions")
    assert pe.open_risk([Position(symbol="SOLUSD", side="long", quantity=1, entry_price=1,
                                  stop_loss=None, unrealized_pnl=0)], [], c)[1] == "stop_unknown"


SPEC = {"ticket_id": "t1", "venue_symbol": "SOLUSD", "side": "long", "quantity": 0.5,
        "stop_loss": 118.0, "take_profit": 126.0, "limit_price": 120.0}


def _o(**kw):
    d = dict(symbol="SOLUSD", side="long", quantity=0.5, price=120.0, stop_loss=118.0, take_profit=126.0,
             order_id="O1")
    d.update(kw)
    return WorkingOrder(**d)


def _p(**kw):
    d = dict(symbol="SOLUSD", side="long", quantity=0.5, entry_price=120.0, stop_loss=118.0,
             take_profit=126.0, unrealized_pnl=0.0)
    d.update(kw)
    return Position(**d)


@pytest.mark.parametrize("positions,orders,want", [
    ([], [_o()], "placed"),
    ([_p()], [], "open"),
    ([], [_o(stop_loss=None)], "partial_no_sl_tp"),
    ([_p(take_profit=None)], [], "partial_no_sl_tp"),
    ([], [_o(), _o(order_id="O2")], "duplicate"),
    ([], [], "not_found"),
    ([], [_o(symbol="ETHUSD")], "not_found"),
])
def test_classify_confirmation(positions, orders, want):
    found = pe.match_terminal(SPEC, positions, orders)
    assert pe.classify_confirmation(SPEC, found) == want


def test_open_from_fills_keeps_the_newest_row_per_identity():
    fills = [
        {"id": 1, "account_id": "b", "symbol": "SOLUSDT", "direction": "long", "status": "open", "created_at": "1"},
        {"id": 2, "account_id": "b", "symbol": "SOLUSDT", "direction": "long", "status": "closed", "created_at": "2"},
        {"id": 3, "account_id": "b", "symbol": "ETHUSDT", "direction": "short", "status": "filled", "created_at": "1"},
    ]
    got = pe.open_from_fills(fills)
    assert [r["id"] for r in got] == [3]


# ── the cycle, against a fake adapter and a fake API ──────────────────────


class FakeAdapter:
    def __init__(self, account=None, positions=(), orders=(), after_submit=None, attempt=None, read_error=None):
        self.account = account or acct()
        self.positions, self.orders = list(positions), list(orders)
        self.after_submit = after_submit       # (positions, orders) the terminal shows after submit
        self.attempt = attempt
        self.read_error = read_error
        self.calls = []

    def read_account(self, page):
        return self.account

    def read_positions(self, page):
        if self.read_error:
            raise LookupError(self.read_error)
        return list(self.positions)

    def read_orders(self, page):
        return list(self.orders)

    def place_bracket(self, page, spec, *, arm=False):
        self.calls.append(("place_bracket", spec.ticket_id, arm))
        if self.attempt is not None:
            return self.attempt
        if arm:
            if self.after_submit is not None:
                self.positions, self.orders = self.after_submit
            return PlaceAttempt(stage="submitted", submitted=True)
        return PlaceAttempt(stage="form_verified", detail="disarmed")

    def cancel_order(self, page, order, *, arm=False):
        self.calls.append(("cancel_order", order.order_id, arm))
        return {"ok": True, "clicked": arm}

    def flatten(self, page, symbol=None, *, arm=False):
        self.calls.append(("flatten", symbol, arm))
        return {"ok": True, "clicked": arm}

    def modify_bracket(self, page, position, sl, tp, *, arm=False):
        self.calls.append(("modify_bracket", position.symbol, arm))
        return {"ok": True, "clicked": arm}


class FakeApi:
    def __init__(self, tickets=(), fills=(), fail_post=False):
        self._tickets, self._fills = list(tickets), list(fills)
        self.posts = []
        self.fail_post = fail_post

    def tickets(self, account_id):
        return list(self._tickets)

    def open_fills(self, account_id):
        return pe.open_from_fills(self._fills)

    def post_report(self, body):
        if self.fail_post:
            raise OSError("api down")
        self.posts.append(body)
        return {"ok": True}


@pytest.fixture
def env(tmp_path):
    ledger = pe.IntentLedger(tmp_path / "ledger.jsonl")
    state = pe.ExecutorState(tmp_path)
    state.save({"day": pe.trading_day(NOW), "day_start_captured": 5000.0})
    return ledger, state


def run(adapter, api, env, mode="live", **kw):
    ledger, state = env
    return pe.run_cycle(adapter=adapter, page=None, api=api, cfg=cfg(), mode=mode, ledger=ledger,
                        state=state, now=NOW, **kw)


def test_off_reads_nothing(env):
    ad = FakeAdapter(read_error="must not be read")
    res = run(ad, FakeApi([ticket()]), env, mode="off")
    assert res.actions[0]["what"] == "off" and ad.calls == []


def test_read_only_clicks_nothing_and_writes_nothing(env):
    ad, api = FakeAdapter(), FakeApi([ticket()])
    res = run(ad, api, env, mode="read_only")
    assert ad.calls == [] and api.posts == []
    assert any(a["what"] == "would_click" for a in res.actions)
    assert env[0].latest() == {}


def test_dry_run_walks_to_a_verified_form_and_stops(env):
    ad, api = FakeAdapter(), FakeApi([ticket()])
    res = run(ad, api, env, mode="read_only", walk_form=True)
    assert ad.calls == [("place_bracket", "prop-manual-aaa", False)]
    walk = [a for a in res.actions if a["what"] == "would_click"][0]["walk"]
    assert walk["stage"] == "form_verified" and walk["submitted"] is False
    assert api.posts == []


def test_dry_run_stops_at_the_guard_when_the_ticket_does_not_fit(env):
    ad = FakeAdapter(account=acct(4724.0, 4724.0))
    env[1].save({"day": pe.trading_day(NOW), "day_start_captured": 4724.0})
    res = run(ad, FakeApi([ticket(qty=37.5)]), env, mode="read_only", walk_form=True)
    assert ad.calls == []
    g = [a for a in res.actions if a["what"] == "guards"][0]
    assert g["fits"] is False


def test_live_writes_the_ledger_before_the_click(env):
    ledger, _ = env
    seen = {}

    class Spy(FakeAdapter):
        def place_bracket(self, page, spec, *, arm=False):
            seen["state_at_click"] = ledger.state(spec.ticket_id)
            return super().place_bracket(page, spec, arm=arm)

    ad = Spy(after_submit=([], [_o(quantity=0.5)]))
    api = FakeApi([ticket()])
    run(ad, api, env)
    assert seen["state_at_click"] == "intended"
    assert ledger.state("prop-manual-aaa") == "placed"
    kinds = [(p["kind"], p.get("status")) for p in api.posts]
    assert ("fill", "placed") in kinds and ("account_status", None) in kinds


def test_confirmation_is_by_reread_not_click_success(env):
    # Submit "succeeds" but nothing appears: never reported placed.
    ad, api = FakeAdapter(after_submit=([], [])), FakeApi([ticket()])
    run(ad, api, env)
    assert env[0].state("prop-manual-aaa") == "unconfirmed"
    assert not any(p.get("status") in ("placed", "open") for p in api.posts)


def test_ticket_id_is_the_idempotency_key(env):
    ad, api = FakeAdapter(after_submit=([], [_o()])), FakeApi([ticket()])
    run(ad, api, env)
    run(ad, api, env)
    assert [c for c in ad.calls if c[0] == "place_bracket"] == [("place_bracket", "prop-manual-aaa", True)]


def test_at_most_one_ticket_per_cycle(env):
    ad = FakeAdapter(after_submit=([], [_o()]))
    api = FakeApi([ticket(), ticket(ticket_id="prop-manual-bbb", created_at=(NOW + timedelta(seconds=1)).isoformat())])
    run(ad, api, env)
    assert len([c for c in ad.calls if c[0] == "place_bracket"]) == 1


def test_guard_refusal_reports_skipped_with_the_reason(env):
    ad = FakeAdapter(account=acct(4724.0, 4724.0))
    env[1].save({"day": pe.trading_day(NOW), "day_start_captured": 4724.0})
    api = FakeApi([ticket(qty=37.5)])
    run(ad, api, env)
    skip = [p for p in api.posts if p.get("status") == "skipped"][0]
    assert "static DD" in skip["reason"] and skip["ticket_id"] == "prop-manual-aaa"
    assert ad.calls == []


def test_expired_ticket_reports_skipped_expired(env):
    ad, api = FakeAdapter(), FakeApi([ticket(valid_until=(NOW - timedelta(minutes=1)).isoformat())])
    run(ad, api, env)
    assert [p["reason"] for p in api.posts if p.get("status") == "skipped"] == ["expired"]


# § 3.5 table, row by row


def test_selector_drift_halts_before_any_write(env):
    ad, api = FakeAdapter(read_error="no positions table"), FakeApi([ticket()])
    res = run(ad, api, env)
    assert res.halted and ad.calls == [] and api.posts == []


def test_partial_click_places_the_missing_leg_once_then_closes(env):
    ledger, _ = env
    ledger.record("t1", "submitted", spec=SPEC)
    ad = FakeAdapter(positions=[_p(stop_loss=None)])
    run(ad, FakeApi(), env)
    assert ("modify_bracket", "SOLUSD", True) in ad.calls
    ad.calls.clear()
    run(ad, FakeApi(), env)
    assert ("flatten", "SOLUSD", True) in ad.calls and ledger.state("t1") == "contained"


def test_partial_working_order_without_bracket_is_cancelled(env):
    env[0].record("t1", "submitted", spec=SPEC)
    ad = FakeAdapter(orders=[_o(stop_loss=None)])
    run(ad, FakeApi(), env)
    assert ("cancel_order", "O1", True) in ad.calls


def test_duplicate_submit_cancels_the_extra_order(env):
    env[0].record("t1", "submitted", spec=SPEC)
    ad = FakeAdapter(orders=[_o(order_id="O1"), _o(order_id="O2")])
    res = run(ad, FakeApi(), env)
    assert ("cancel_order", "O2", True) in ad.calls and ("cancel_order", "O1", True) not in ad.calls
    assert any("duplicate" in a for a in res.alerts)


def test_duplicate_fill_alerts_and_halts(env):
    env[0].record("t1", "submitted", spec=SPEC)
    ad = FakeAdapter(positions=[_p(), _p()])
    res = run(ad, FakeApi(), env)
    assert any("operator must close the excess" in a for a in res.alerts) and res.halted


def test_timeout_after_submit_never_resubmits_and_skips_after_n_reads(env):
    ledger, _ = env
    ledger.record("t1", "submitted", spec=SPEC)
    ad, api = FakeAdapter(), FakeApi([])
    for _ in range(3):
        run(ad, api, env)
    assert ledger.state("t1") == "skipped"
    assert [p["reason"] for p in api.posts if p.get("status") == "skipped"] == ["unconfirmed_submit"]
    assert not any(c[0] == "place_bracket" for c in ad.calls)


def test_unresolved_submit_holds_new_entries(env):
    env[0].record("t1", "submitted", spec=SPEC)
    ad = FakeAdapter()
    res = run(ad, FakeApi([ticket()]), env)
    assert any(a["what"] == "hold" for a in res.actions) and not any(c[0] == "place_bracket" for c in ad.calls)


def test_disconnect_with_a_position_open_needs_nothing(env):
    # The read fails: nothing is clicked or written; the broker bracket protects.
    ad = FakeAdapter(read_error="net::ERR_INTERNET_DISCONNECTED")
    res = run(ad, FakeApi(fills=[{"id": 1, "account_id": "breakout_1", "symbol": "SOLUSDT",
                                   "direction": "long", "status": "open"}]), env)
    assert res.halted and ad.calls == []


def test_orphan_position_halts_and_is_never_touched(env):
    ad = FakeAdapter(positions=[_p()])
    res = run(ad, FakeApi([ticket()]), env)
    assert res.halted and "orphan" in res.halted
    assert ad.calls == [] and env[1].halted()


def test_journal_open_but_terminal_closed_reports_closed_after_two_reads(env):
    fills = [{"id": 1, "account_id": "breakout_1", "symbol": "SOLUSDT", "direction": "long",
              "status": "open", "ticket_id": "tx", "sl": 118, "tp": 126}]
    api = FakeApi([], fills)
    run(FakeAdapter(), api, env)
    assert not [p for p in api.posts if p.get("status") == "closed"]
    run(FakeAdapter(), api, env)
    assert [p["ticket_id"] for p in api.posts if p.get("status") == "closed"] == ["tx"]


def test_sl_tp_differs_reports_amend(env):
    fills = [{"id": 1, "account_id": "breakout_1", "symbol": "SOLUSDT", "direction": "long",
              "status": "open", "sl": 117.0, "tp": 126.0}]
    api = FakeApi([], fills)
    run(FakeAdapter(positions=[_p(stop_loss=118.0)]), api, env)
    amend = [p for p in api.posts if p.get("kind") == "amend"][0]
    assert amend["sl"] == 118.0 and amend["direction"] == "long"


def test_write_back_failure_is_an_alert_not_a_crash(env):
    res = run(FakeAdapter(), FakeApi([], fail_post=True), env)
    assert any("write-back failed" in a for a in res.alerts)


def test_not_submitted_attempt_is_skipped_not_confirmed(env):
    ad = FakeAdapter(attempt=PlaceAttempt(stage="refused", detail="one-click trading is on"))
    api = FakeApi([ticket()])
    run(ad, api, env)
    assert env[0].state("prop-manual-aaa") == "refused"
    assert "one-click" in [p for p in api.posts if p.get("status") == "skipped"][0]["reason"]


def test_ledger_survives_a_torn_last_line(tmp_path):
    led = pe.IntentLedger(tmp_path / "l.jsonl")
    led.record("a", "intended")
    with open(tmp_path / "l.jsonl", "a") as fh:
        fh.write('{"ticket_id": "b", "sta')
    assert led.state("a") == "intended" and led.state("b") is None


# ── dxtrade order controls: pure helpers ──────────────────────────────────


def test_check_bracket_spec():
    ok = BracketSpec("t", "SOLUSD", "long", 1.0, 118.0, 126.0, "limit", 120.0)
    assert check_bracket_spec(ok) == []
    bad = BracketSpec("t", "SOLUSD", "short", 1.0, 118.0, 126.0, "limit", 120.0)
    assert check_bracket_spec(bad)


def test_verify_form_values():
    assert verify_form_values({"quantity": {"value": "0.5"}}, {"quantity": 0.5}) == []
    assert verify_form_values({"quantity": {"value": "5"}}, {"quantity": 0.5})
    assert verify_form_values({}, {"stop_loss": 1.0}) == ["stop_loss: not readable back"]


def test_verify_form_selection_never_assumes_a_side():
    ok = {"selected": {"side": "buy", "order_type": "limit"}}
    assert verify_form_selection(ok, "long", "limit") == []
    assert verify_form_selection(ok, "short", "limit") == ["side: chose sell, form shows buy"]
    assert verify_form_selection(ok, "long", "market") == ["order_type: chose market, form shows limit"]
    assert verify_form_selection({"selected": {"side": None, "order_type": None}}, "long", "limit") == [
        "side: not readable back", "order_type: not readable back"]
    assert verify_form_selection({}, "long", "limit") == ["side: not readable back", "order_type: not readable back"]
    assert verify_form_selection({"selected": {"side": "ambiguous", "order_type": "limit"}}, "long", "limit") == [
        "side: chose buy, form shows ambiguous"]


def test_form_names_symbol_is_a_whole_word():
    assert form_names_symbol({"form_text": "New order SOLUSD 0.5"}, "solusd")
    assert not form_names_symbol({"form_text": "New order SOLUSDX"}, "SOLUSD")
    assert not form_names_symbol({}, "SOLUSD")


def test_classify_ticket_surface():
    assert classify_ticket_surface({"found": True}) == "dom"
    assert classify_ticket_surface({"found": False, "canvases_in_form": 2}) == "canvas_ticket"
    assert classify_ticket_surface({"found": False}) == "not_found"


# ── dxtrade order controls in a real Chromium, against an INVENTED form ───

TICKET_PAGE = """
<html><body>
<div class="bar"><span>One-click trading</span><input type="checkbox" id="oc" %s></div>
<table><thead><tr class="instrument"><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
<tbody><tr class="instrument" data-row-id="1"><td>SOLUSD</td><td>119.9</td><td>120.0</td></tr></tbody></table>
<div class="chart"><button>Sell 119.9</button><button>120.0 Buy</button></div>
<button onclick="document.getElementById('ticket').style.display='block'">New Order</button>
<script>
function sel(b){document.querySelectorAll('[data-g='+b.dataset.g+']').forEach(x=>x.removeAttribute('aria-pressed'));b.setAttribute('aria-pressed','true');
  if(b.dataset.g==='side'){document.getElementById('sub').textContent='Place '+b.textContent+' Order'}}
</script>
<div id="ticket" style="display:none">
  <div class="hdr">SOLUSD</div>
  <button data-g="side" onclick="window.__side='buy';sel(this)">Buy</button><button data-g="side" onclick="window.__side='sell';sel(this)">Sell</button>
  <button data-g="type" onclick="window.__type='market';sel(this)">Market</button><button data-g="type" onclick="window.__type='limit';sel(this)">Limit</button>
  <div><span>Quantity</span><input id="q"></div>
  <div><span>Price</span><input id="px"></div>
  <div><label><input type="checkbox" id="slc" onclick="document.getElementById('sl').disabled=!this.checked">Stop Loss</label>
       <input id="sl" aria-label="Stop Loss price" disabled></div>
  <div><label><input type="checkbox" id="tpc" onclick="document.getElementById('tp').disabled=!this.checked">Take Profit</label>
       <input id="tp" aria-label="Take Profit price" disabled></div>
  <button id="sub" onclick="window.__submits=(window.__submits||0)+1">Place Order</button>
  <button onclick="document.getElementById('ticket').style.display='none'">Cancel</button>
</div>
</body></html>
"""


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    import glob
    candidates = [None] + sorted(glob.glob("/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell"))
    with sync_api.sync_playwright() as pw:
        b, errors = None, []
        for exe in candidates:
            try:
                b = pw.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
                break
            except Exception as exc:
                errors.append(str(exc).splitlines()[0][:120])
        if b is None:
            pytest.skip(f"chromium unavailable: {errors}")
        yield b
        b.close()


@pytest.fixture
def tpage(browser):
    def make(one_click_checked=False, html=None):
        p = browser.new_page()
        p.set_content(html or (TICKET_PAGE % ("checked" if one_click_checked else "")))
        return p
    return make


SOL = BracketSpec("t1", "SOLUSD", "long", 0.5, 118.0, 126.0, "limit", 120.0)


def test_probe_reports_a_dom_ticket_and_types_nothing(tpage):
    p = tpage()
    got = DXtradeAdapter(timeout_ms=3_000).probe_order_ticket(p, "SOLUSD")
    assert got["surface"] == "dom" and got["via"] == "button:New Order"
    assert set(got["fields"]) >= {"quantity", "price", "stop_loss", "take_profit"}
    assert got["buttons"]["submit"] == "Place Order"
    assert p.evaluate("window.__submits") is None and p.evaluate("window.__side") is None
    assert p.evaluate("document.getElementById('q').value") == ""


# One-click trading is a DIAGNOSTIC, not a gate (operator 2026-09-28: "As long
# we know we're placing trades correctly on the sidebar ticket, we don't need
# to consider the one click toggle at all"). Its reading is recorded on every
# result; nothing refuses on it.


def test_probe_records_one_click_on_and_still_opens_the_ticket(tpage):
    p = tpage(one_click_checked=True)
    got = DXtradeAdapter(timeout_ms=3_000).probe_order_ticket(p, "SOLUSD")
    assert got["surface"] == "dom" and got["via"] == "button:New Order" and got["refused"] is None
    assert got["one_click"]["state"] == "on"
    assert got["selected"] == {"side": None, "order_type": None}   # untouched form: nothing pressed yet
    assert p.evaluate("window.__submits") is None and p.evaluate("document.getElementById('q').value") == ""


def test_one_click_unreadable_is_recorded_not_gated(tpage):
    # The live terminal's measured case (#13711): the label is missing / the
    # toggle is custom-styled, so the state reads "unknown". The ticket path
    # goes through the sidebar form and never a chart price button, so this
    # blocks nothing — the attempt carries the reading for the run log.
    p = tpage(html=TICKET_PAGE.replace("One-click trading", "Something else") % "")
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "submitted" and att.submitted is True, att.detail
    assert p.evaluate("window.__submits") == 1
    assert att.form["one_click"]["state"] == "unknown"
    pub = pe._attempt_public(att)
    assert pub["one_click"] == {"state": "unknown", "why": "label not found"}


def test_one_click_on_is_recorded_on_a_disarmed_walk(tpage):
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(tpage(one_click_checked=True), SOL)
    assert att.stage == "form_verified" and att.form["one_click"]["state"] == "on"


# ── six-field read-back before submit (symbol, side, type, qty, SL, TP) ───


def test_place_bracket_refuses_when_side_is_not_readable_back(tpage):
    # side buttons that expose no pressed/selected state at all
    html = (TICKET_PAGE % "").replace("window.__side='buy';sel(this)", "window.__side='buy'") \
                             .replace("window.__side='sell';sel(this)", "window.__side='sell'")
    p = tpage(html=html)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "side: not readable back" in att.detail
    assert p.evaluate("window.__submits") is None
    assert p.evaluate("document.getElementById('ticket').style.display") == "none"  # closed


def test_place_bracket_refuses_when_the_form_shows_the_other_side(tpage):
    # clicking Buy lights up Sell: the form disagrees with what we chose
    html = (TICKET_PAGE % "").replace("window.__side='buy';sel(this)", "window.__side='buy';sel(this.nextElementSibling)")
    p = tpage(html=html)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "side: chose buy, form shows sell" in att.detail
    assert p.evaluate("window.__submits") is None


def test_place_bracket_refuses_when_order_type_is_not_readable_back(tpage):
    html = (TICKET_PAGE % "").replace("window.__type='market';sel(this)", "window.__type='market'") \
                             .replace("window.__type='limit';sel(this)", "window.__type='limit'")
    p = tpage(html=html)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "order_type: not readable back" in att.detail
    assert p.evaluate("window.__submits") is None


def test_side_reads_back_from_a_buy_prefixed_submit_button(tpage):
    # A terminal whose submit button is "Buy SOLUSD" and whose side buttons carry no state:
    # the submit text is the side read-back, and the symbol is named by it too.
    html = (TICKET_PAGE % "").replace("window.__side='buy';sel(this)", "window.__side='buy'") \
                             .replace("window.__side='sell';sel(this)", "window.__side='sell'") \
                             .replace(">Place Order<", ">Buy SOLUSD<")
    p = tpage(html=html)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL)
    assert att.stage == "form_verified", att.detail
    assert att.form["selected"] == {"side": "buy", "order_type": "limit"}
    short = BracketSpec("t2", "SOLUSD", "short", 0.5, 126.0, 118.0, "limit", 120.0)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(tpage(html=html), short, arm=True)
    assert att.stage == "refused" and "side: chose sell, form shows buy" in att.detail


def test_place_bracket_refuses_when_the_symbol_changes_after_typing(tpage):
    # the header flips to another instrument when quantity is typed
    html = (TICKET_PAGE % "").replace(
        '<input id="q">', "<input id=\"q\" oninput=\"document.querySelector('.hdr').textContent='ETHUSD'\">")
    p = tpage(html=html)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "symbol: form no longer names SOLUSD" in att.detail
    assert p.evaluate("window.__submits") is None


def test_canvas_only_ticket_is_the_feasibility_stop(tpage):
    html = TICKET_PAGE.replace('<div><span>Quantity</span><input id="q"></div>', "<canvas></canvas>")
    got = DXtradeAdapter(timeout_ms=3_000).probe_order_ticket(tpage(html=html % ""), "SOLUSD")
    assert got["surface"] in ("canvas_ticket", "not_opened")


def test_place_bracket_disarmed_fills_verifies_and_never_submits(tpage):
    p = tpage()
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL)
    assert att.stage == "form_verified" and att.submitted is False, att.detail
    assert p.evaluate("window.__submits") is None
    assert p.evaluate("document.getElementById('sl').value") == "118"
    assert p.evaluate("window.__side") == "buy" and p.evaluate("window.__type") == "limit"
    assert p.evaluate("document.getElementById('ticket').style.display") == "none"  # closed


def test_place_bracket_armed_clicks_submit_exactly_once(tpage):
    p = tpage()
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.submitted is True and p.evaluate("window.__submits") == 1


def test_place_bracket_refuses_a_form_for_another_symbol(tpage):
    p = tpage(html=(TICKET_PAGE % "").replace('<div class="hdr">SOLUSD</div>', '<div class="hdr">SOLUSDX</div>'))
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "does not name" in att.detail
    assert p.evaluate("window.__submits") is None


def test_place_bracket_refuses_on_read_back_mismatch(tpage):
    # an input that drops everything after the decimal point
    html = (TICKET_PAGE % "").replace('<input id="q">',
                                      '<input id="q" oninput="this.value=this.value.split(\'.\')[0]">')
    p = tpage(html=html)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "mismatch" in att.detail
    assert p.evaluate("window.__submits") is None


def test_place_bracket_refuses_ambiguous_fields(tpage):
    dup = '<input id="tp" aria-label="Take Profit price" disabled></div>'
    assert dup in TICKET_PAGE
    html = (TICKET_PAGE % "").replace(dup, dup + '<div><span>Take Profit</span><input id="tp2"></div>')
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(tpage(html=html), SOL, arm=True)
    assert att.stage == "refused" and "ambiguous" in att.detail


def test_chart_price_buttons_are_never_an_opener(tpage):
    html = (TICKET_PAGE % "").replace(">New Order<", ">Something<")
    p = tpage(html=html)
    got = DXtradeAdapter(timeout_ms=3_000).open_order_ticket(p, "ZZZUSD")
    assert got["opened"] is False
    assert p.evaluate("window.__submits") is None


def test_cancel_order_needs_one_row_and_is_disarmed_by_default(tpage):
    html = ("<table><thead><tr><th>Sts</th><th>Symbol</th><th>Order ID</th><th></th></tr></thead><tbody>"
            "<tr><td>W</td><td>SOLUSD</td><td>O1</td><td><button title='Cancel' "
            "onclick='window.__cancel=1'>×</button></td></tr></tbody></table>")
    p = tpage(html=html)
    a = DXtradeAdapter(timeout_ms=3_000)
    r = a.cancel_order(p, WorkingOrder(symbol="SOLUSD", order_id="O1"))
    assert r["ok"] and not r["clicked"] and p.evaluate("window.__cancel") is None
    r = a.cancel_order(p, WorkingOrder(symbol="SOLUSD", order_id="O1"), arm=True)
    assert r["clicked"] and p.evaluate("window.__cancel") == 1
    assert a.cancel_order(p, WorkingOrder(symbol="SOLUSD", order_id=None), arm=True)["ok"] is False


def test_flatten_requires_a_symbol():
    assert DXtradeAdapter().flatten(None, None, arm=True)["ok"] is False


def test_executor_files_never_print_credentials():
    src = (Path(__file__).resolve().parents[1] / "scripts" / "prop" / "prop_executor_tick.py").read_text()
    assert "print(username" not in src and "print(password" not in src
    assert "tracing" not in src.lower()


# ── breach_guards: report (operator 2026-09-28, breakout_1) ───────────────


def test_real_config_breakout_1_is_report():
    assert pe.load_config("breakout_1").breach_guards == "report"


def test_report_mode_places_through_a_breach_and_reports_it():
    c = cfg(breach_guards="report")
    v = guards(t=ticket(qty=37.5), account=acct(4724.0, 4724.0), ds=4724.0, c=c)
    assert v.fits, v.reasons
    assert any(b.startswith("static DD") for b in v.breach_reports)
    assert any(b.startswith("risk gate: exceeds_cushion") for b in v.breach_reports)


def test_report_mode_unknown_day_start_is_a_report_not_a_refusal():
    v = guards(ds=None, c=cfg(breach_guards="report"))
    assert v.fits and any("day-start balance unknown" in b for b in v.breach_reports)


@pytest.mark.parametrize("kw,needle", [
    (dict(orisk=None, ostate="stop_unknown"), "could not look"),
    (dict(account=AccountSnapshot(balance=None, equity=None)), "could not look"),
    (dict(t=ticket(sl=None)), "lacks SL or TP"),
    (dict(t=ticket(qty=40.0), account=acct(9000.0, 9000.0), ds=9000.0), "risk cap"),
    (dict(halted="orphan"), "halted"),
])
def test_report_mode_keeps_the_hard_refusals(kw, needle):
    v = guards(c=cfg(breach_guards="report"), **kw)
    assert not v.fits and any(needle in r for r in v.reasons), v.reasons


def test_report_mode_cycle_places_and_alerts(tmp_path):
    ledger, state = pe.IntentLedger(tmp_path / "l.jsonl"), pe.ExecutorState(tmp_path)
    state.save({"day": pe.trading_day(NOW), "day_start_captured": 4724.0})
    ad = FakeAdapter(account=acct(4724.0, 4724.0), after_submit=([], [_o(quantity=37.5)]))
    api = FakeApi([ticket(qty=37.5)])
    res = pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(breach_guards="report"), mode="live",
                       ledger=ledger, state=state, now=NOW)
    assert ("place_bracket", "prop-manual-aaa", True) in ad.calls
    assert any("breach_guards=report, placing anyway" in a for a in res.alerts)


# ── the round trip ────────────────────────────────────────────────────────


def test_quote_from_tables():
    from src.prop.platform.dxtrade import quote_from_tables
    tables = [{"headers": ["Symbol", "Bid", "Ask", "Change"], "rows": [["ETHUSD", "2,682.48", "2,682.49", "-1"]]}]
    assert quote_from_tables(tables, "ETHUSD") == {"bid": 2682.48, "ask": 2682.49}
    assert quote_from_tables(tables, "SOLUSD") is None
    assert quote_from_tables([{"headers": ["Symbol", "Price"], "rows": [["ETHUSD", "1"]]}], "ETHUSD") is None


def test_parse_price_joins_split_spans_only_around_one_decimal_point():
    from src.prop.platform.dxtrade import parse_price, quote_from_tables
    assert parse_price("184.25") == 184.25
    assert parse_price("184.\n25") == 184.25            # "###." + raised "##" (#13855)
    assert parse_price("184.25\n3") == 184.253          # pipette span
    assert parse_price("184\n25") is None                # no point: never 18425
    assert parse_price("1.8\n4.25") is None              # two points
    assert parse_price(None) is None
    tables = [{"headers": ["Symbol", "Bid", "Ask"], "rows": [["SOLUSD", "184.\n24", "184.\n26"]]}]
    assert quote_from_tables(tables, "SOLUSD") == {"bid": 184.24, "ask": 184.26}


# The MEASURED watchlist shape (#13898): a header-only table and a separate
# header-less row table, plus an Orders table that also names SOLUSD.
SPLIT_WATCHLIST = """<html><body>
<table><thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th><th>Change</th><th>Chg%</th><th>Description</th><th></th></tr></thead><tbody></tbody></table>
<table><tbody>
 <tr class="instrument"><td>ETHUSD</td><td>2682.48</td><td>2682.49</td><td>-1</td><td>-0.1%</td><td>Ether</td><td></td></tr>
 <tr class="instrument"><td>SOLUSD</td><td>%BID%</td><td>%ASK%</td><td>0.5</td><td>0.3%</td><td>Solana</td><td></td></tr>
</tbody></table>
<table><thead><tr><th>Sts</th><th>Symbol</th><th>Side</th></tr></thead>
 <tbody><tr data-row-id="1"><td>W</td><td>SOLUSD</td><td>Buy</td></tr></tbody></table>
</body></html>"""


def test_read_quote_reads_the_virtualised_watchlist_rows(tpage):
    p = tpage(html=SPLIT_WATCHLIST.replace("%BID%", "184.24").replace("%ASK%", "184.26"))
    ad = DXtradeAdapter(timeout_ms=3_000)
    assert ad.read_quote(p, "SOLUSD") == {"bid": 184.24, "ask": 184.26}
    assert ad.read_quote(p, "BTCUSD") is None


def test_read_quote_refuses_a_misaligned_or_implausible_row(tpage):
    ad = DXtradeAdapter(timeout_ms=3_000)
    wide = tpage(html=SPLIT_WATCHLIST.replace("%BID%", "100").replace("%ASK%", "184.26"))
    assert ad.read_quote(wide, "SOLUSD") is None                    # 45% spread: not a quote
    short = tpage(html=SPLIT_WATCHLIST.replace('<td>Solana</td><td></td>', '<td>Solana</td>')
                  .replace("%BID%", "184.24").replace("%ASK%", "184.26"))
    assert ad.read_quote(short, "SOLUSD") is None                   # cell count != header count


def test_quote_from_watchlist_rows_needs_the_symbol_under_the_symbol_header():
    from src.prop.platform.dxtrade import quote_from_watchlist_rows
    got = {"headers": ["Symbol", "Bid", "Ask"], "rows": [["184.2", "SOLUSD", "184.3"]]}
    assert quote_from_watchlist_rows(got, "SOLUSD") is None
    assert quote_from_watchlist_rows({"headers": None, "rows": [["SOLUSD", "1", "1"]]}, "SOLUSD") is None


def test_quote_diagnostics_shows_raw_cells_of_the_symbol_row_only():
    from src.prop.platform.dxtrade import quote_diagnostics
    tables = [{"kind": "grid", "headers": ["Symbol", "Bid", "Ask"], "rows": [["SOLUSD", "x\ny", "?"], ["ETHUSD", "1", "2"]]},
              {"kind": "table", "headers": ["Symbol", "Qty", "P&L"], "rows": [["SOLUSD", "1", "5"]]}]
    d = quote_diagnostics(tables, "SOLUSD")
    assert [t["headers"] for t in d["symbol_tables"]] == [["Symbol", "Bid", "Ask"], ["Symbol", "Qty", "P&L"]]
    assert d["rows"] == [["'SOLUSD'", "'x\\ny'", "'?'"]]      # positions rows are never dumped


class RTAdapter(FakeAdapter):
    """A terminal where a submit opens a bracketed position and a flatten
    closes it (unless told otherwise)."""

    def __init__(self, *, fill=True, legs=True, close_works=True, quote=None, **kw):
        super().__init__(**kw)
        self.fill, self.legs, self.close_works = fill, legs, close_works
        self.quote = {"bid": 100.0, "ask": 100.1} if quote is None else quote

    def read_quote(self, page, venue):
        return self.quote

    def place_bracket(self, page, spec, *, arm=False):
        self.calls.append(("place_bracket", spec.ticket_id, arm))
        if arm and self.fill:
            self.positions = [Position(symbol=spec.venue_symbol, side=spec.side, quantity=spec.quantity,
                                       entry_price=100.1, unrealized_pnl=-0.02,
                                       stop_loss=spec.stop_loss if self.legs else None,
                                       take_profit=spec.take_profit if self.legs else None)]
        return PlaceAttempt(stage="submitted" if arm else "form_verified", submitted=arm)

    def flatten(self, page, symbol=None, *, arm=False):
        self.calls.append(("flatten", symbol, arm))
        if arm and self.close_works:
            self.positions = []
        return {"ok": True, "clicked": arm}


def rt_cfg(**kw):
    return cfg(symbols={"SOLUSDT": {"venue": "SOLUSD", "cvpp": 1.0, "lot_units": 1.0, "min_lots": 0.01,
                                    "lot_step": 0.01}},
               watched_click_max_lots={"SOLUSD": 0.1}, **kw)


def rt(ad, api, tmp_path, arm=True, c=None, **kw):
    return pe.run_round_trip(adapter=ad, page=None, api=api, cfg=c or rt_cfg(),
                             ledger=pe.IntentLedger(tmp_path / "l.jsonl"), venue_symbol="SOLUSD",
                             arm=arm, now=NOW, **kw)


def test_round_trip_places_confirms_closes_confirms_and_reports_both(tmp_path):
    ad, api = RTAdapter(), FakeApi()
    res = rt(ad, api, tmp_path)
    assert res.halted is None, res.actions
    assert [c[0] for c in ad.calls] == ["place_bracket", "flatten"]
    fills = [(p["status"], p["direction"], p["qty"]) for p in api.posts]
    assert fills == [("open", "long", 0.1), ("closed", "long", 0.1)]
    spec = [a for a in res.actions if a["what"] == "round_trip_spec"][0]
    assert spec["spec"]["stop_loss"] < 100.1 < spec["spec"]["take_profit"]
    assert pe.IntentLedger(tmp_path / "l.jsonl").state(spec["spec"]["ticket_id"]) == "closed"


def test_round_trip_dry_clicks_nothing(tmp_path):
    ad, api = RTAdapter(), FakeApi()
    rt(ad, api, tmp_path, arm=False)
    assert ad.calls == [("place_bracket", ad.calls[0][1], False), ("flatten", "SOLUSD", False)]
    assert api.posts == []


@pytest.mark.parametrize("setup,needle", [
    (dict(c=cfg(watched_click_max_lots={"SOLUSD": 0.1},
                symbols={"SOLUSDT": {"venue": "SOLUSD", "cvpp": 1.0, "lot_units": None,
                                     "lot_step": None, "min_lots": None}})), "not declared"),
    (dict(c=cfg(symbols=rt_cfg().symbols)), "no executor.watched_click_max_lots"),
    (dict(lots=0.5), "must be > 0 and <="),
])
def test_round_trip_refuses_before_any_click(tmp_path, setup, needle):
    ad = RTAdapter()
    res = rt(ad, FakeApi(), tmp_path, **setup)
    assert needle in (res.halted or "") and ad.calls == []


def test_round_trip_without_a_quote_logs_the_watchlist_and_clicks_nothing(tmp_path):
    ad = RTAdapter(quote={})
    ad.quote_diagnostics = lambda page, venue: {"rows": [["'SOLUSD'"]]}
    res = rt(ad, FakeApi(), tmp_path, arm=False)
    assert "no bid/ask for SOLUSD" in (res.halted or "") and ad.calls == []
    diag = [a for a in res.actions if a["what"] == "quote_diagnostics"]
    assert diag and diag[0]["watchlist"] == {"rows": [["'SOLUSD'"]]}


def test_round_trip_refuses_when_the_symbol_is_already_in_use(tmp_path):
    ad = RTAdapter(positions=[_p()])
    res = rt(ad, FakeApi(), tmp_path)
    assert "already has a position" in res.halted and ad.calls == []


def test_round_trip_refuses_without_a_quote(tmp_path):
    ad = RTAdapter(quote={})
    assert "no bid/ask" in rt(ad, FakeApi(), tmp_path).halted and ad.calls == []


def test_round_trip_never_closes_blind_when_the_entry_is_not_seen(tmp_path):
    ad, api = RTAdapter(fill=False), FakeApi()
    res = rt(ad, api, tmp_path, reads=2)
    assert "stopped at entry" in res.halted
    assert not any(c[0] == "flatten" for c in ad.calls)
    assert not any(p.get("status") == "open" for p in api.posts)


def test_round_trip_missing_leg_is_contained_not_closed_as_a_success(tmp_path):
    ad, api = RTAdapter(legs=False), FakeApi()
    res = rt(ad, api, tmp_path)
    assert "partial_no_sl_tp" in res.halted
    assert ("modify_bracket", "SOLUSD", True) in ad.calls
    assert not any(p.get("status") == "closed" for p in api.posts)


def test_round_trip_close_not_confirmed_alerts_and_does_not_report_closed(tmp_path):
    ad, api = RTAdapter(close_works=False), FakeApi()
    res = rt(ad, api, tmp_path, reads=2)
    assert res.halted == "round trip: close not confirmed"
    assert [p["status"] for p in api.posts] == ["open"]
    assert any("close it by hand" in a for a in res.alerts)


def test_tick_round_trip_modes():
    from scripts.prop.prop_executor_tick import resolve_mode
    ns = lambda **k: SimpleNamespace(**{"probe_ticket": "", "dry_run": False, "watched_click": False,  # noqa: E731
                                        "round_trip": "", "live": False, **k})
    assert resolve_mode(ns(round_trip="ETHUSD"), {}) == "round_trip_dry"
    assert resolve_mode(ns(round_trip="ETHUSD", live=True), {}) == "not_armed"
    assert resolve_mode(ns(round_trip="ETHUSD", live=True), {pe.MODE_ENV: "live"}) == "round_trip_live"


# ── manager review of #13647 (2026-09-28) ─────────────────────────────────


@pytest.mark.parametrize("env", [{}, {pe.MODE_ENV: "read_only"}, {pe.MODE_ENV: "liev"}, {pe.MODE_ENV: "off"}])
def test_manual_live_runs_need_live_explicitly(env):
    from scripts.prop.prop_executor_tick import resolve_mode
    ns = lambda **k: SimpleNamespace(**{"probe_ticket": "", "dry_run": False, "watched_click": False,  # noqa: E731
                                        "round_trip": "", "live": False, **k})
    assert resolve_mode(ns(watched_click=True), env) == "not_armed"
    assert resolve_mode(ns(round_trip="ETHUSD", live=True), env) == "not_armed"
    assert resolve_mode(ns(watched_click=True), {pe.MODE_ENV: "live"}) == "live"
    assert resolve_mode(ns(round_trip="ETHUSD", live=True), {pe.MODE_ENV: "live"}) == "round_trip_live"


def test_not_armed_exits_before_any_browser(monkeypatch, capsys):
    from scripts.prop import prop_executor_tick as tick
    monkeypatch.delenv(pe.MODE_ENV, raising=False)
    assert tick.main(["--watched-click"]) == tick.EXIT_ERROR
    assert '"not_armed"' in capsys.readouterr().out


def test_cycle_refuses_a_symbol_already_open_on_the_terminal(env):
    for i, kw in enumerate((dict(positions=[_p()]), dict(orders=[_o(order_id="X")]))):
        ledger, state = env
        ad = FakeAdapter(**kw)
        api = FakeApi([ticket(ticket_id=f"t-busy-{i}")], fills=[{"id": 1, "account_id": "breakout_1", "symbol": "SOLUSDT",
                                          "direction": "long", "status": "open", "sl": 118, "tp": 126}])
        res = pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(breach_guards="report"), mode="live",
                           ledger=ledger, state=state, now=NOW)
        g = [a for a in res.actions if a["what"] == "guards"][0]
        assert not g["fits"] and any("already has" in r for r in g["reasons"])
        assert not any(c[0] == "place_bracket" for c in ad.calls)


def test_capped_size_is_re_stepped():
    c = cfg(symbols={"SOLUSDT": {"venue": "SOLUSD", "cvpp": 1.0, "lot_units": 1.0, "min_lots": 0.05,
                                 "lot_step": 0.01}})
    spec, facts, why = pe.bracket_from_ticket(ticket(qty=10), c, max_lots=0.057)
    assert spec.quantity == 0.05
    spec, _, why = pe.bracket_from_ticket(ticket(qty=10), c, max_lots=0.02)
    assert spec is None and "below the venue minimum" in why


def test_round_trip_refuses_an_off_step_size(tmp_path):
    ad = RTAdapter()
    res = rt(ad, FakeApi(), tmp_path, lots=0.055)
    assert "not a valid venue size" in res.halted and ad.calls == []


def test_live_output_never_carries_the_raw_form(env):
    ad = FakeAdapter(attempt=PlaceAttempt(stage="refused", detail="x", form={"form_text": "Account 823528, USD"}))
    res = pe.run_cycle(adapter=ad, page=None, api=FakeApi([ticket()]), cfg=cfg(), mode="live",
                       ledger=env[0], state=env[1], now=NOW)
    assert "823528" not in json.dumps(res.actions, default=str)


def test_real_config_eth_sol_lots_are_the_measured_ones():
    c = pe.load_config("breakout_1")
    for s in ("ETHUSDT", "SOLUSDT"):
        assert (c.symbols[s]["lot_units"], c.symbols[s]["lot_step"], c.symbols[s]["min_lots"]) == (1, 0.01, None)


def test_one_click_dump_reads_structure_never_values(tpage):
    html = ("<div class='bar'><div class='tg'><span>One-click trading</span>"
            "<div class='sw' data-state='off' style='background:rgb(1,2,3)'><div class='knob'></div></div></div>"
            "<input id='secret' value='hunter2-account-823528'></div>")
    p = tpage(html=html)
    a = DXtradeAdapter(timeout_ms=3_000)
    assert a.read_one_click(p)["state"] == "unknown"      # no checkbox/aria: the measured case
    dump = a.one_click_dump(p)
    blob = json.dumps(dump)
    assert dump["found"] and "hunter2" not in blob and "823528" not in blob
    kids = dump["levels"][0]["children"]
    assert any(k["attrs"].get("data-state") == "off" for k in kids)
    assert any(k["is_label"] for k in kids)
    probe = a.probe_order_ticket(p, "SOLUSD")
    assert probe["surface"] == "not_opened" and probe["one_click_dump"]["found"]


def test_controls_dump_masks_digits_and_never_reads_values(tpage):
    """Probe 2026-09-28 (#13742): no opener matched on the live terminal, which
    tags controls with data-test-id. The control map must carry no account
    number, balance or input value (it goes to a PUBLIC run log)."""
    html = ("<div data-test-id='account_balance'>Balance 5,012.34 acct 823528</div>"
            "<button data-test-id='trade_panel_toggle' style='position:fixed;top:4px;right:6px;width:20px;height:20px'>"
            "<svg width='16' height='16'><path d='M0 0L8 8'/></svg></button>"
            "<button data-test-id='order_entry_open' aria-label='Order 77'>Trade</button>"
            "<button title='Help'>Contact jane.doe@example.com</button>"
            "<div class='header-user-menu'>Jane Doe</div>"
            "<button data-test-id='account_menu' aria-label='Jane Doe'>Jane Doe</button>"
            "<div role='tab' aria-selected='true' data-test-id='side_buy'>Buy</div>"
            "<div><span>Quantity</span><input data-test-id='qty' value='hunter2-823528'></div>"
            "<table><tbody><tr><td data-test-id='row'>SOLUSD 120.5</td></tr></tbody></table>")
    p = tpage(html=html)
    dump = DXtradeAdapter(timeout_ms=3_000).controls_dump(p)
    icon = next(c for c in dump["controls"] if c["tid"] == "trade_panel_toggle")
    assert icon["svg"] is True and icon["box"][1] == 4 and icon["right_gap"] == 6 and icon["text"] == ""
    blob = json.dumps([{k: v for k, v in c.items() if k not in ("box", "right_gap")} for c in dump["controls"]])
    assert dump["found"] and "hunter2" not in blob and not any(c.isdigit() for c in blob)
    assert "@" not in blob and "jane" not in blob.lower() and "<email>" in blob
    menu = next(c for c in dump["controls"] if c["tid"] == "account_menu")
    assert menu == {"tag": "button", "tid": "account_menu", "personal": True}
    tids = {c["tid"] for c in dump["controls"]}
    assert {"account_balance", "order_entry_open", "side_buy", "qty"} <= tids and "row" not in tids
    side = next(c for c in dump["controls"] if c["tid"] == "side_buy")
    assert side["state"]["aria-selected"] == "true" and side["role"] == "tab"
    assert next(c for c in dump["controls"] if c["tid"] == "qty")["label"] == "Quantity"
    probe = DXtradeAdapter(timeout_ms=3_000).probe_order_ticket(p, "SOLUSD")
    assert probe["surface"] == "not_opened" and probe["controls_dump"]["found"]


# Shaped like the sidebar MEASURED in probe #13760 (test ids symbol_input, BUY,
# SELL; Market/Limit/Stop/OCO buttons; SL/TP inputs behind data-value toggles).
SIDEBAR = """
<div class='hdr'><button data-test-id='account_menu'>Jane Doe</button></div>
<div class='panel'>
  <div><span>Symbol</span><input data-test-id='symbol_input' value='SOLUSD'></div>
  <div><button class='tp-a sel' style='background:rgb(1,2,3)'>Market</button><button class='tp-a'>Limit</button>
       <button class='tp-a'>Stop</button><button class='tp-a'>OCO</button></div>
  <div><button data-test-id='SELL' class='sd'>Sell</button>
       <button data-test-id='BUY' class='sd on' style='background:rgb(0,128,0)'>Buy</button></div>
  <div><span>Quantity</span><input value='823528'></div>
  <div>Stop Loss <div data-value='false'></div><input value='117.5'><button>Price</button></div>
  <div>Take Profit <div data-value='false'></div><input value='126.25'><button>Price</button></div>
  <div>Projected P&amp;L 12.34 contact jane.doe@example.com</div>
  <button class='submit'>Place Order</button>
</div>
"""


def test_ticket_panel_dump_reads_the_measured_sidebar_shape_and_redacts(tpage):
    p = tpage(html=SIDEBAR)
    a = DXtradeAdapter(timeout_ms=3_000)
    dump = a.ticket_panel_dump(p)
    assert dump["found"] and not dump["truncated"]
    blob = json.dumps(dump["rows"])
    assert "823528" not in blob and "117" not in blob and "@" not in blob and "jane" not in blob.lower()
    texts = [r.get("text") for r in dump["rows"]]
    assert {"Stop Loss", "Take Profit", "Quantity", "Market", "Buy", "Place Order"} <= set(texts)
    buy = next(r for r in dump["rows"] if r.get("tid") == "BUY")
    sell = next(r for r in dump["rows"] if r.get("tid") == "SELL")
    assert buy["style"]["bg"] != sell["style"]["bg"] and buy["cls"] == "sd on"
    toggles = [r for r in dump["rows"] if (r.get("state") or {}).get("data-value") == "false"]
    assert len(toggles) == 2
    assert not any(r.get("tid") == "account_menu" for r in dump["rows"])   # outside the panel
    probe = a.probe_order_ticket(p, "SOLUSD")
    assert probe["ticket_panel"]["found"] and "controls_dump" not in probe
    assert p.evaluate("document.querySelectorAll('input')[1].value") == "823528"   # nothing typed


def test_ticket_panel_dump_falls_back_to_the_control_map(tpage):
    p = tpage(html="<div><button>Trade</button></div>")
    probe = DXtradeAdapter(timeout_ms=3_000).probe_order_ticket(p, "SOLUSD")
    assert "ticket_panel" not in probe and probe["ticket_panel_why"] and probe["controls_dump"]["found"]


# The live sidebar's shape (probe #13760): SL / TP behind div[data-value]
# toggles that start "false", a "Price" mode button beside each, and a short
# scrollable panel whose submit sits below the fold once the brackets are on.
LIVE_SIDEBAR = """
<html><body style="margin:0">
<script>
function sel(b){document.querySelectorAll('[data-g='+b.dataset.g+']').forEach(x=>x.removeAttribute('aria-pressed'));
  b.setAttribute('aria-pressed','true');
  if(b.dataset.g==='side'){document.getElementById('sub').textContent=b.textContent+' SOLUSD'}}
function tog(d){ if(!d.hasAttribute('data-stuck')) d.setAttribute('data-value', d.getAttribute('data-value')==='true'?'false':'true') }
</script>
<div id="panel" style="height:260px;overflow-y:auto;width:320px">
  <div class="hdr">SOLUSD</div>
  <button data-g="type" onclick="sel(this)">Market</button><button data-g="type" onclick="sel(this)">Limit</button>
  <button data-test-id="SELL" data-g="side" onclick="sel(this)">Sell</button>
  <button data-test-id="BUY" data-g="side" onclick="sel(this)">Buy</button>
  <div><span>Quantity</span><input id="q"></div>
  <div><span>Price</span><input id="px"></div>
  <div><span>Stop Loss</span><div id="slt" data-value="false" onclick="tog(this)" style="width:26px;height:16px"></div>
       <input id="sl"><button>%SLMODE%</button></div>
  <div><span>Take Profit</span><div id="tpt" data-value="false" onclick="tog(this)" style="width:26px;height:16px"></div>
       <input id="tp"><button>Price</button></div>
  <div style="height:400px"></div>
  %SUBMIT%
</div>
</body></html>
"""
SUBMIT_BTN = '<button id="sub" onclick="window.__submits=(window.__submits||0)+1">Place Order</button>'


def _live(slmode="Price", submit=SUBMIT_BTN, extra=""):
    return LIVE_SIDEBAR.replace("%SLMODE%", slmode).replace("%SUBMIT%", submit) + extra


def test_live_shape_switches_both_toggles_on_scrolls_to_submit_and_verifies(tpage):
    p = tpage(html=_live())
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL)
    assert att.stage == "form_verified", att.detail
    assert p.evaluate("document.getElementById('slt').dataset.value") == "true"
    assert p.evaluate("document.getElementById('tpt').dataset.value") == "true"
    assert att.form["submit"]["scrolled"] is True and att.form["submit"]["text"] == "Buy SOLUSD"
    assert p.evaluate("window.__submits") is None


def test_live_shape_armed_clicks_the_scrolled_submit_once(tpage):
    p = tpage(html=_live())
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.submitted is True and p.evaluate("window.__submits") == 1, att.detail


def test_a_toggle_left_off_refuses_never_submits_naked(tpage):
    p = tpage(html=_live().replace('id="tpt" data-value="false"', 'id="tpt" data-value="false" data-stuck="1"'))
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "take_profit: enabling toggle is OFF" in att.detail
    assert p.evaluate("window.__submits") is None


def test_a_bracket_mode_other_than_price_refuses(tpage):
    p = tpage(html=_live(slmode="Pips"))
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "stop_loss: entry mode is 'Pips'" in att.detail
    assert p.evaluate("window.__submits") is None


def test_a_virtualised_submit_is_found_by_scrolling_the_panel(tpage):
    # The submit is rendered only once the panel is scrolled near its end.
    lazy = ("<script>document.getElementById('panel').addEventListener('scroll', e => {"
            " const p = e.target; if (p.scrollTop + p.clientHeight > p.scrollHeight - 120 && !document.getElementById('sub')) {"
            " p.insertAdjacentHTML('beforeend', " + json.dumps(SUBMIT_BTN) + "); } });</script>")
    p = tpage(html=_live(submit="") + lazy)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL)
    assert att.stage == "form_verified", att.detail


def test_a_field_changed_by_scrolling_refuses(tpage):
    # A panel that rewrites the quantity when scrolled: the post-scroll read-back must catch it.
    evil = ("<script>document.getElementById('panel').addEventListener('scroll', () => {"
            " document.getElementById('q').value = '9'; });</script>")
    p = tpage(html=_live() + evil)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "read-back after scrolling to submit" in att.detail
    assert p.evaluate("window.__submits") is None


def test_a_submit_that_does_not_name_the_side_refuses(tpage):
    p = tpage(html=_live().replace("b.textContent+' SOLUSD'", "'Place Order'"))
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "does not name the intended side" in att.detail
    assert p.evaluate("window.__submits") is None


def test_verify_bracket_legs_pure():
    import src.prop.platform.dxtrade as pe_dx
    on = [{"field": "stop_loss", "checked": True}, {"field": "take_profit", "checked": True}]
    f = {"toggle_candidates": 2, "checkboxes": on, "fields": {"stop_loss": {"mode": "Price"}, "take_profit": {"mode": "Price"}}}
    assert pe_dx.verify_bracket_legs(f) == []
    assert pe_dx.verify_bracket_legs({**f, "checkboxes": on[:1]}) == ["take_profit: 0 enabling toggles found (need exactly 1)"]
    assert pe_dx.verify_bracket_legs({"fields": {}}) == []            # a form with no toggles at all
    assert pe_dx.verify_bracket_legs({**f, "fields": {"stop_loss": {"mode": "Price"}, "take_profit": {}}}) \
        == ["take_profit: entry mode is None, need 'Price'"]


def test_ticket_panel_dump_anchors_on_the_sidebar_not_the_work_area(tpage):
    # Probe #13775: the work area holds a watchlist table AND the sidebar; the
    # sidebar (here with only 2 inputs) must be the anchor and tables skipped.
    rows = "".join(f"<tr><td>SYM{i}</td><td><button>Sell</button></td><td><button>Buy</button></td></tr>"
                   for i in range(200))
    html = ("<div class='workarea'><table><tbody>" + rows + "</tbody></table>"
            "<div class='side'><input data-test-id='symbol_input'>"
            "<button data-test-id='SELL'>Sell</button><button data-test-id='BUY'>Buy</button>"
            "<div><span>Quantity</span><input></div><button>Place Order</button></div></div>")
    dump = DXtradeAdapter(timeout_ms=3_000).ticket_panel_dump(tpage(html=html))
    assert dump["found"] and not dump["truncated"]
    texts = [r.get("text") for r in dump["rows"]]
    assert "Quantity" in texts and "Place Order" in texts and not any(t and t.startswith("SYM") for t in texts)


# ── auto-revert triggers (operator go-live criteria, 2026-09-28) ──────────


def test_one_read_error_does_not_trip_two_consecutive_do(env):
    _, state = env
    ad = FakeAdapter(read_error="no positions table")
    run(ad, FakeApi([ticket()]), env)
    assert state.halted() is None
    res = run(ad, FakeApi([ticket()]), env)
    assert state.halted() and "2 consecutive ticks" in state.halted()
    assert any(a.startswith("AUTO-REVERT") for a in res.alerts)
    assert ad.calls == []


def test_a_clean_read_resets_the_error_count(env):
    _, state = env
    run(FakeAdapter(read_error="x"), FakeApi(), env)
    run(FakeAdapter(), FakeApi(), env)                    # clean cycle in between
    run(FakeAdapter(read_error="x"), FakeApi(), env)
    assert state.halted() is None


def test_read_only_never_writes_the_latch(env):
    _, state = env
    for _ in range(3):
        res = run(FakeAdapter(read_error="x"), FakeApi(), env, mode="read_only")
    assert state.halted() is None and any(a.startswith("AUTO-REVERT") for a in res.alerts)


def test_partial_no_sl_tp_after_submit_trips_at_once(env):
    _, state = env
    ad = FakeAdapter(after_submit=([_p(stop_loss=None, quantity=0.5)], []))
    res = run(ad, FakeApi([ticket()]), env)
    assert state.halted() and "partial_no_sl_tp" in state.halted()
    assert any(a.startswith("AUTO-REVERT") for a in res.alerts)


def test_partial_found_by_a_later_reconcile_trips(env):
    ledger, state = env
    ledger.record("t1", "submitted", spec=SPEC)
    run(FakeAdapter(positions=[_p(stop_loss=None)]), FakeApi(), env)
    assert state.halted() and "t1: partial_no_sl_tp" in state.halted()


def test_duplicate_trips(env):
    ledger, state = env
    ledger.record("t1", "submitted", spec=SPEC)
    run(FakeAdapter(orders=[_o(order_id="O1"), _o(order_id="O2")]), FakeApi(), env)
    assert state.halted() and "duplicate" in state.halted()


def test_unconfirmed_placement_trips_after_the_configured_reads(env):
    ledger, state = env
    ledger.record("t1", "submitted", spec=SPEC)
    for _ in range(cfg().unconfirmed_reads):
        run(FakeAdapter(), FakeApi(), env)
    assert ledger.state("t1") == "skipped"
    assert state.halted() and "unconfirmed placement" in state.halted()


def test_two_consecutive_readback_refusals_trip(env):
    _, state = env
    bad = PlaceAttempt(stage="refused", detail="form read-back mismatch: quantity: typed 0.5, form shows 5")
    run(FakeAdapter(attempt=bad), FakeApi([ticket()]), env)
    assert state.halted() is None
    run(FakeAdapter(attempt=bad), FakeApi([ticket(ticket_id="prop-manual-bbb")]), env)
    assert state.halted() and "2 consecutive read-back refusals" in state.halted()


def test_a_non_readback_refusal_does_not_count(env):
    _, state = env
    other = PlaceAttempt(stage="refused", detail="no unique submit control in the form")
    run(FakeAdapter(attempt=other), FakeApi([ticket()]), env)
    run(FakeAdapter(attempt=other), FakeApi([ticket(ticket_id="prop-manual-bbb")]), env)
    assert state.halted() is None


def test_a_tripped_latch_refuses_the_next_ticket(env):
    _, state = env
    state.halt("AUTO-REVERT: test")
    ad = FakeAdapter()
    run(ad, FakeApi([ticket()]), env)
    assert not any(c[0] == "place_bracket" for c in ad.calls)


def test_record_tick_error_trips_on_the_second(tmp_path):
    assert pe.record_tick_error(tmp_path, True, "TimeoutError") is None
    trip = pe.record_tick_error(tmp_path, True, "TimeoutError")
    assert trip and "2 consecutive ticks" in trip and pe.ExecutorState(tmp_path).halted()
    assert pe.record_tick_error(tmp_path / "ro", False, "x") is None
    assert pe.record_tick_error(tmp_path / "ro", False, "x") is None
    assert pe.ExecutorState(tmp_path / "ro").halted() is None


def test_ticket_panel_dump_ignores_the_charts_symbol_input(tpage):
    # Probe #13797: the chart widget has its own symbol_input, first in DOM
    # order; the dump must anchor on the ticket (from its BUY button).
    html = ("<div class='layout'><div class='chart'><input data-test-id='symbol_input' placeholder='Symbol...'>"
            + "".join(f"<div>row {i}</div>" for i in range(400)) + "</div>"
            "<div class='side'><input data-test-id='symbol_input'>"
            "<button data-test-id='SELL'>Sell</button><button data-test-id='BUY'>Buy</button>"
            "<div><span>Quantity</span><input></div><button>Place Order</button></div></div>")
    dump = DXtradeAdapter(timeout_ms=3_000).ticket_panel_dump(tpage(html=html))
    assert dump["found"] and not dump["truncated"] and dump["n"] < 20
    texts = [r.get("text") for r in dump["rows"]]
    assert "Quantity" in texts and "Place Order" in texts and not any(t and t.startswith("row") for t in texts)


def test_ticket_panel_dump_refuses_two_buy_buttons(tpage):
    html = ("<div><input data-test-id='symbol_input'><button data-test-id='BUY'>Buy</button>"
            "<button data-test-id='BUY'>Buy</button><button data-test-id='SELL'>Sell</button></div>")
    dump = DXtradeAdapter(timeout_ms=3_000).ticket_panel_dump(tpage(html=html))
    assert dump["found"] is False and "2 [data-test-id=BUY]" in dump["why"]


# A replica of the order-ticket sidebar MEASURED in probe #13816: selection is
# shown ONLY by background colour; the quantity label is the previous sibling
# of the input's container; SL / TP inputs and their "Price" select are
# disabled until their data-value toggle (placed BEFORE the label) is on; the
# visible text says "SOL", the symbol_input carries the venue symbol; the
# submit sits in a footer BELOW the fields' panel, in a short scrolling column.
MEASURED_SIDEBAR = """
<html><body style="margin:0">
<style>.tb{background:rgb(64,66,94)} .row{display:flex}</style>
<script>
const SEL = {type: 'rgb(80, 87, 179)', BUY: 'rgb(26, 143, 109)', SELL: 'rgb(200, 50, 50)'};
function pickType(b){document.querySelectorAll('.tp').forEach(x=>x.style.background='');b.style.background=SEL.type}
function pickSide(b){document.querySelectorAll('.sd').forEach(x=>x.style.background='');
  b.style.background=SEL[b.dataset.testId];document.getElementById('sub').textContent=b.textContent+' %SYM%'}
function tog(d){ if(d.hasAttribute('data-stuck')) return; const on=d.getAttribute('data-value')!=='true';
  d.setAttribute('data-value', on?'true':'false');
  d.closest('.blk').querySelectorAll('input,button').forEach(x=>x.disabled=!on) }
</script>
<div id="col" style="position:absolute;left:903px;top:0;width:330px;height:300px;overflow-y:auto">
 <div id="panel">
  <section id="s1">
   <div><div>Symbol</div><div><div><input data-test-id="symbol_input" value="%SYMVAL%"></div></div></div>
   <div>SOL</div>
   <div class="row">
    <button class="tb tp" onclick="pickType(this)" style="background:rgb(80, 87, 179)"><span>Market</span></button>
    <button class="tb tp" onclick="pickType(this)"><span>Limit</span></button>
    <button class="tb tp" onclick="pickType(this)"><span>Stop</span></button>
    <button class="tb tp" onclick="pickType(this)"><span>OCO</span></button>
   </div>
  </section>
  <section id="s2">
   <div class="row">
    <button class="tb sd" data-test-id="SELL" onclick="pickSide(this)"><span>Sell</span></button>
    <button class="tb sd" data-test-id="BUY" onclick="pickSide(this)" style="background:rgb(26, 143, 109)"><span>Buy</span></button>
   </div>
   <div>
    <div>Lots x 1 SOL</div>
    <div><div><input id="q" inputmode="numeric" step="0.01" min="0.01"><button></button><button></button></div></div>
   </div>
   <div><div>Current Price (Ask)</div><span>120.00</span></div>
  </section>
  <section id="s3">
   <div>Protection</div>
   <div class="blk">
    <div class="row"><div id="slt" data-value="false" onclick="tog(this)" style="width:26px;height:16px"></div><div>Stop Loss:</div></div>
    <div class="row"><div><div><input id="sl" inputmode="numeric" disabled><button disabled></button></div></div>
      <button disabled><div><div>%SLMODE%</div></div></button></div>
    <div><div>Pips</div><div>0.0</div></div>
   </div>
   <div class="blk">
    <div class="row"><div id="tpt" data-value="false" onclick="tog(this)" style="width:26px;height:16px"></div><div>Take Profit:</div></div>
    <div class="row"><div><div><input id="tp" inputmode="numeric" disabled><button disabled></button></div></div>
      <button disabled><div><div>Price</div></div></button></div>
    <div><div>Pips</div><div>0.0</div></div>
   </div>
  </section>
 </div>
 <div id="footer" style="padding-top:200px"><button id="sub" onclick="window.__submits=(window.__submits||0)+1">Buy %SYM%</button></div>
</div>
</body></html>
"""


def _measured(sym="SOLUSD", symval="SOLUSD", slmode="Price"):
    return MEASURED_SIDEBAR.replace("%SYMVAL%", symval).replace("%SYM%", sym).replace("%SLMODE%", slmode)


def test_measured_sidebar_is_recognised_labels_selection_symbol_submit(tpage):
    p = tpage(html=_measured())
    form = DXtradeAdapter(timeout_ms=3_000)._find_form(p)
    assert form["found"], form
    assert form["fields"]["quantity"]["label"].startswith("Lots x")
    assert form["fields"]["stop_loss"]["label"] == "Stop Loss:" and form["fields"]["take_profit"]["label"] == "Take Profit:"
    assert form["selected"] == {"side": "buy", "order_type": "market"}      # by background only
    assert form["symbol_value"] == "SOLUSD" and form["submit_outside_form"] is True
    assert form["buttons"]["submit"] == "Buy SOLUSD"
    assert [c["field"] for c in form["checkboxes"]] == ["stop_loss", "take_profit"]


def test_measured_sidebar_disarmed_walk_passes_the_full_read_back(tpage):
    p = tpage(html=_measured())
    spec = BracketSpec("t1", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec)
    assert att.stage == "form_verified", att.detail
    assert p.evaluate("document.getElementById('slt').dataset.value") == "true"
    assert p.evaluate("document.getElementById('tpt').dataset.value") == "true"
    assert att.form["submit"]["scrolled"] is True and att.form["submit"]["text"] == "Buy SOLUSD"
    assert p.evaluate("window.__submits") is None


def test_measured_sidebar_sell_is_selected_and_read_back_by_colour(tpage):
    p = tpage(html=_measured())
    spec = BracketSpec("t2", "SOLUSD", "short", 0.01, 126.0, 118.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec)
    assert att.stage == "form_verified", att.detail
    assert att.form["selected"]["side"] == "sell" and att.form["submit"]["text"] == "Sell SOLUSD"


def test_measured_sidebar_refuses_another_symbol(tpage):
    p = tpage(html=_measured(sym="ETHUSD", symval="ETHUSD"))
    spec = BracketSpec("t3", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec, arm=True)
    assert att.stage == "refused" and "does not name SOLUSD" in att.detail
    assert p.evaluate("window.__submits") is None


def test_measured_sidebar_stuck_toggle_refuses(tpage):
    p = tpage(html=_measured().replace('id="tpt" data-value="false"', 'id="tpt" data-value="false" data-stuck="1"'))
    spec = BracketSpec("t4", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec, arm=True)
    assert att.stage == "refused" and p.evaluate("window.__submits") is None


def _measured_live_label(clamp_min=None):
    """The replica with the MEASURED label shape (probe #13855): the terminal
    writes "<Side> <qty> SOLUSD at <price>" from the quantity it accepted;
    ``clamp_min`` models a venue that silently raises a smaller size."""
    js = ("<script>function relabel(){const b=document.querySelector('.sd[style*=background]');"
          "let q=parseFloat(document.getElementById('q').value)||0;"
          + (f"if(q>0&&q<{clamp_min})q={clamp_min};" if clamp_min else "") +
          "document.getElementById('sub').textContent=(b?b.textContent.trim():'Buy')+' '+q+' SOLUSD at 120.05'}"
          "document.addEventListener('input',relabel);document.addEventListener('click',()=>setTimeout(relabel,0));</script>")
    return _measured().replace("</body>", js + "</body>")


def test_submit_label_mismatch_reads_the_measured_label_shape():
    from src.prop.platform.dxtrade import submit_label_mismatch
    spec = BracketSpec("t", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    assert submit_label_mismatch("Buy 0.01 SOLUSD at 120.05", spec) == ""
    assert submit_label_mismatch("Buy SOLUSD", spec) == ""                 # no qty stated: read-back alone
    assert "states quantity 0.1" in submit_label_mismatch("Buy 0.1 SOLUSD at 120.05", spec)
    assert "names 'ETHUSD'" in submit_label_mismatch("Buy 0.01 ETHUSD at 3000.1", spec)


def test_measured_sidebar_label_stating_the_typed_qty_passes(tpage):
    p = tpage(html=_measured_live_label())
    spec = BracketSpec("t5", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec)
    assert att.stage == "form_verified", att.detail


def test_measured_sidebar_label_with_a_clamped_qty_refuses(tpage):
    # A venue that clamps a below-minimum size up shows it here; the dry run at
    # the lot step is a venue-minimum measurement only because this refuses.
    p = tpage(html=_measured_live_label(clamp_min=0.1))
    spec = BracketSpec("t6", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec, arm=True)
    assert att.stage == "refused" and "states quantity 0.1" in att.detail, att.detail
    assert p.evaluate("window.__submits") is None


def test_colour_selection_needs_a_unique_unselected_colour(tpage):
    # Every toggle a different colour: no majority "unselected" colour → not readable.
    html = _measured().replace('class="tb tp" onclick="pickType(this)"><span>Limit', 'class="tp" style="background:rgb(1,1,1)" onclick="pickType(this)"><span>Limit') \
                      .replace('class="tb tp" onclick="pickType(this)"><span>Stop', 'class="tp" style="background:rgb(2,2,2)" onclick="pickType(this)"><span>Stop') \
                      .replace('class="tb tp" onclick="pickType(this)"><span>OCO', 'class="tp" style="background:rgb(3,3,3)" onclick="pickType(this)"><span>OCO') \
                      .replace('class="tb sd" data-test-id="SELL"', 'class="sd" style="background:rgb(4,4,4)" data-test-id="SELL"')
    form = DXtradeAdapter(timeout_ms=3_000)._find_form(tpage(html=html))
    assert form["selected"]["order_type"] is None


def test_probe_on_the_measured_sidebar_reports_submit_candidates(tpage):
    p = tpage(html=_measured())
    got = DXtradeAdapter(timeout_ms=3_000).probe_order_ticket(p, "SOLUSD")
    assert got["surface"] == "dom" and got["symbol_value"] == "SOLUSD" and got["submit_outside_form"] is True
    tp_ = got["ticket_panel"]
    assert any(c["text"] == "Buy SOLUSD" for c in tp_["submit_candidates"])
    assert {"step": "0.01", "min": "0.01"}.items() <= next(c for c in tp_["qty_constraints"]).items()
    assert any(pp["overflow_y"] == "auto" for pp in tp_["panel_parents"])
    assert p.evaluate("window.__submits") is None


# ── SOL-only go-live: a non-enabled symbol is SKIPPED, never refused ──────


def _sol_only():
    c = cfg()
    c.enabled_venue_symbols = ["SOLUSD"]
    return c


def test_a_non_enabled_symbol_is_skipped_before_the_form_and_counts_nothing(env):
    ledger, state = env
    eth = ticket(ticket_id="prop-eth", symbol="ETHUSDT")
    ad, api = FakeAdapter(), FakeApi([eth])
    st0 = dict(state.load())
    c = _sol_only()
    c.symbols = {**c.symbols, "ETHUSDT": {"venue": "ETHUSD", "cvpp": 1.0, "lot_units": 1.0,
                                          "min_lots": 0.01, "lot_step": 0.01}}
    res = pe.run_cycle(adapter=ad, page=None, api=api, cfg=c, mode="live", ledger=ledger,
                       state=state, now=NOW)
    assert not any(c[0] == "place_bracket" for c in ad.calls)
    assert ledger.state("prop-eth") == "skipped" and ledger.latest()["prop-eth"]["reason"] == "symbol_not_enabled"
    assert [p["reason"] for p in api.posts if p.get("status") == "skipped"] == ["symbol_not_enabled"]
    st1 = state.load()
    assert st1.get("readback_refusals", 0) == st0.get("readback_refusals", 0) and state.halted() is None
    assert res.halted is None


def test_a_skipped_symbol_does_not_block_the_next_enabled_ticket(env):
    ledger, _ = env
    eth = ticket(ticket_id="prop-eth", symbol="ETHUSDT")
    sol = ticket(ticket_id="prop-sol", created_at=(NOW + timedelta(seconds=1)).isoformat())
    ad = FakeAdapter(after_submit=([], [_o(quantity=0.5)]))
    pe.run_cycle(adapter=ad, page=None, api=FakeApi([eth, sol]), cfg=_sol_only(), mode="live",
                 ledger=ledger, state=env[1], now=NOW)
    assert [c[1] for c in ad.calls if c[0] == "place_bracket"] == ["prop-sol"]


def test_enabled_venues_env_overrides_config_and_missing_enables_nothing():
    assert pe.enabled_venues({"enabled_venue_symbols": ["SOLUSD"]}, env={}) == ["SOLUSD"]
    assert pe.enabled_venues({"enabled_venue_symbols": ["SOLUSD"]}, env={"PROP_EXECUTOR_SYMBOLS": "solusd, ethusd"}) \
        == ["ETHUSD", "SOLUSD"]
    assert pe.enabled_venues({}, env={}) == []


def test_the_real_config_enables_sol_only():
    c = pe.load_config("breakout_1")
    assert c.enabled_venue_symbols == ["SOLUSD"]


def test_round_trip_refuses_a_non_enabled_venue(env):
    ledger, _ = env
    c = _sol_only()
    c.symbols = {**c.symbols, "ETHUSDT": {"venue": "ETHUSD", "cvpp": 1.0, "lot_units": 1.0,
                                          "min_lots": 0.01, "lot_step": 0.01}}
    c.watched_click_max_lots = {"ETHUSD": 0.01, "SOLUSD": 0.01}
    res = pe.run_round_trip(adapter=FakeAdapter(), page=None, api=FakeApi(), cfg=c, ledger=ledger,
                            venue_symbol="ETHUSD", arm=False)
    assert res.halted and "enabled_venue_symbols" in res.halted


# ── Fable review of #13822 ─────────────────────────────────────────────────


def test_outside_submit_must_sit_below_the_form_not_above(tpage):
    # A same-column "Buy …" control ABOVE the fields (a quick-trade widget),
    # no submit below: never tagged as the submit.
    html = _measured().replace('<div id="footer" style="padding-top:200px"><button id="sub" '
                               'onclick="window.__submits=(window.__submits||0)+1">Buy SOLUSD</button></div>', '') \
                      .replace('<div id="panel">',
                               '<button id="quick" onclick="window.__quick=1">Buy 120.05</button><div id="panel">')
    p = tpage(html=html)
    form = DXtradeAdapter(timeout_ms=3_000)._find_form(p)
    assert "submit" not in form["buttons"]
    spec = BracketSpec("t9", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec, arm=True)
    assert att.stage == "refused" and p.evaluate("window.__quick") is None


def test_the_click_targets_the_token_proven_element(tpage):
    p = tpage(html=_measured())
    spec = BracketSpec("t10", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec, arm=True)
    assert att.submitted and att.form["submit"]["token"].startswith("t")
    assert p.evaluate("document.getElementById('sub').dataset.metisSubmitToken") == att.form["submit"]["token"]


def test_live_shape_requires_a_readable_mode():
    import src.prop.platform.dxtrade as dx
    on = [{"field": "stop_loss", "checked": True}, {"field": "take_profit", "checked": True}]
    f = {"toggle_candidates": 2, "data_value_toggles": 2, "checkboxes": on, "fields": {"stop_loss": {}, "take_profit": {}}}
    bad = dx.verify_bracket_legs(f)
    assert "stop_loss: entry mode is None, need 'Price'" in bad and len(bad) == 2
