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
    assert resolve_mode(ns(watched_click=True), {}) == "live"
    assert resolve_mode(ns(watched_click=True), {pe.MODE_ENV: "off"}) == "off"
    assert resolve_mode(ns(probe_ticket="SOLUSD"), {}) == "probe"


def test_real_config_loads_and_keeps_flat_75_and_unmeasured_lots_refuse():
    c = pe.load_config("breakout_1")
    assert c.risk_cap_usd == 75.0 and c.max_dd_pct == 0.06 and c.daily_loss_pct == 0.03
    assert c.symbols["SOLUSDT"]["lot_units"] is None      # UNMEASURED → refused
    assert c.symbols["ADAUSDT"]["min_lots"] == 10
    spec, _, why = pe.bracket_from_ticket(ticket(), c)
    assert spec is None and "not declared" in why
    assert c.watched_click_max_lots == {}                 # the watched click refuses until set


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
<div id="ticket" style="display:none">
  <div class="hdr">SOLUSD</div>
  <button onclick="window.__side='buy'">Buy</button><button onclick="window.__side='sell'">Sell</button>
  <button onclick="window.__type='market'">Market</button><button onclick="window.__type='limit'">Limit</button>
  <div><span>Quantity</span><input id="q"></div>
  <div><span>Price</span><input id="px"></div>
  <div><label><input type="checkbox" id="slc" onclick="document.getElementById('sl').disabled=!this.checked">Stop Loss</label>
       <input id="sl" aria-label="Stop Loss price" disabled></div>
  <div><span>Take Profit</span><input id="tp"></div>
  <button onclick="window.__submits=(window.__submits||0)+1">Place Order</button>
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


def test_probe_refuses_when_one_click_trading_is_on(tpage):
    p = tpage(one_click_checked=True)
    got = DXtradeAdapter(timeout_ms=3_000).probe_order_ticket(p, "SOLUSD")
    assert got["surface"] == "not_opened" and "one-click trading is on" in got["refused"]


def test_one_click_unreadable_is_treated_as_on(tpage):
    p = tpage(html=TICKET_PAGE.replace("One-click trading", "Something else") % "")
    got = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert got.stage == "refused" and p.evaluate("window.__submits") is None


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
    html = (TICKET_PAGE % "").replace('<div><span>Take Profit</span><input id="tp"></div>',
                                      '<div><span>Take Profit</span><input id="tp"></div>'
                                      '<div><span>Take Profit</span><input id="tp2"></div>')
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
