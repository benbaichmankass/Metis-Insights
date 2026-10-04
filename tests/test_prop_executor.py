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
    if "message" not in kw:
        # The entry band as breakout_ticket renders it (the executor's
        # entry-band check parses it; FakeAdapter's default quote sits inside).
        e = float(t["entry"])
        t["message"] = f"  Entry    : {e}   (only if live price is within {e - 0.5} … {e + 0.5})"
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
    assert resolve_mode(ns(instrument_probe="BTCUSD,ADAUSD"), {}) == "instrument_probe"


def test_real_config_loads_and_keeps_flat_75_and_unmeasured_lots_refuse():
    c = pe.load_config("breakout_1")
    assert c.risk_cap_usd == 75.0 and c.max_dd_pct == 0.06 and c.daily_loss_pct == 0.03
    assert c.symbols["BTCUSDT"]["lot_units"] is None      # UNMEASURED → refused
    assert c.symbols["ADAUSDT"]["min_lots"] == 10
    spec, _, why = pe.bracket_from_ticket(ticket(symbol="BTCUSDT"), c)
    assert spec is None and "not declared" in why
    # The lot step: SOLUSD (#13855); ETHUSD (PROP-ETH-DOM B4) -- enabled since B5 (2026-10-01).
    assert c.watched_click_max_lots == {"SOLUSD": 0.01, "ETHUSD": 0.01}
    assert "ETHUSD" in c.enabled_venue_symbols


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
    assert any(r.startswith("risk cap: ticket asks $80.00") and "REFUSED" in r for r in v.reasons), v.reasons


# ── the flat $-cap vs the venue increments (BREAKOUT-CAP-ROUND, 2026-10-02) ──
#
# The risk the guards grade is computed from the values actually TYPED, and the
# stop can only be typed at the venue's price_step. Rounding it widens the stop
# by up to one step, so a ticket the sizer built at exactly the $75 cap lands a
# few cents above it. Before this, the guard refused that ticket outright:
# prop-manual-00e7ecbd6bd4 (SOLUSDT long, trend_donchian_sol_prop) was skipped
# at 2026-10-02T04:04:36Z for `risk cap: resized (... $75.11 ... cap $75.00)`
# while the announced resize was never applied. The size is now cut instead.

SOL_STEPPED = {"venue": "SOLUSD", "cvpp": 1.0, "lot_units": 1.0, "min_lots": 0.01,
               "lot_step": 0.01, "price_step": 0.01}

# The live ticket's own numbers (GET /api/bot/prop/tickets, ticket_id
# prop-manual-00e7ecbd6bd4): qty 28.45528451 at entry 121.23 / sl 118.59428571
# is exactly $75.00 of risk. floor to 0.01 lots -> 28.45; the stop types as
# 118.59, widening the stop 2.63571429 -> 2.64; 28.45 x 2.64 = $75.11.
ROUNDING_TICKET = dict(entry=121.23, sl=118.59428571, tp=133.23177, qty=28.45528451, risk_usd=75.0)


def test_venue_increment_overshoot_is_resized_down_and_placed():
    c = cfg(symbols={"SOLUSDT": SOL_STEPPED})
    spec, facts, refusal = pe.bracket_from_ticket(ticket(**ROUNDING_TICKET), c)
    assert refusal == ""
    # one lot step at a time, DOWN, until the typed risk fits: 28.45 -> 28.40
    assert (spec.quantity, spec.stop_loss, spec.limit_price) == (28.4, 118.59, 121.23)
    assert facts["ticket_risk_usd"] == 74.98 <= c.risk_cap_usd
    assert facts["risk_cap_resize"]["from_lots"] == 28.45
    assert facts["risk_cap_resize"]["risk_usd_before"] == 75.11
    # and the guard now PLACES it: no risk-cap reason at all
    v = guards(t=ticket(**ROUNDING_TICKET), c=c)
    assert v.fits, v.reasons
    assert not any(r.startswith("risk cap") for r in v.reasons), v.reasons
    assert v.checks["risk_cap"]["action"] == "unchanged"


def test_one_more_lot_step_would_have_breached_the_cap():
    # The cut is to the LAST size that fits, not a safe-looking round number:
    # 28.41 lots x 2.64 = $75.0024, above the cap.
    assert round(28.41 * 2.64, 4) > 75.0
    c = cfg(symbols={"SOLUSDT": SOL_STEPPED})
    _, facts, _ = pe.bracket_from_ticket(ticket(**ROUNDING_TICKET), c)
    assert facts["lots"] == 28.4


def test_a_gross_over_cap_ticket_is_still_refused_never_resized():
    # The ticket itself asks for $120 of risk (no rounding involved). Resizing
    # it to the cap would place a trade nobody sized; it is refused.
    c = cfg(symbols={"SOLUSDT": SOL_STEPPED})
    t = ticket(entry=120.0, sl=118.0, tp=126.0, qty=60.0, risk_usd=120.0)
    spec, facts, refusal = pe.bracket_from_ticket(t, c)
    assert refusal == "" and facts["ticket_risk_usd"] == 120.0
    assert facts["risk_cap_resize"] is None, "a ticket's own over-cap ask is never resized"
    v = guards(t=t, c=c, account=acct(9000.0, 9000.0), ds=9000.0)
    assert not v.fits
    assert any(r.startswith("risk cap: ticket asks $120.00") and "REFUSED" in r for r in v.reasons), v.reasons


def test_the_size_never_grows():
    # A ticket well under the cap is typed as sized — the cap is a ceiling, and
    # size_lots' floor is never walked back up to it.
    c = cfg(symbols={"SOLUSDT": SOL_STEPPED})
    _, facts, _ = pe.bracket_from_ticket(ticket(entry=120.0, sl=118.0, tp=126.0, qty=1.0), c)
    assert facts["lots"] == 1.0 and facts["ticket_risk_usd"] == 2.0
    assert facts["risk_cap_resize"] is None


def test_no_declared_cap_still_refuses_rather_than_emitting_an_unbounded_size():
    c = cfg(risk_cap_usd=None, symbols={"SOLUSDT": SOL_STEPPED})
    _, facts, _ = pe.bracket_from_ticket(ticket(**ROUNDING_TICKET), c)
    assert facts["risk_cap_resize"] is None, "an unknown cap bounds nothing — nothing to resize to"
    v = guards(t=ticket(**ROUNDING_TICKET), c=c)
    assert not v.fits and any("no configured cap" in r for r in v.reasons), v.reasons


def test_a_stop_that_rounds_onto_the_entry_is_refused_not_risk_zero():
    # Otherwise risk reads 0.0 and every cushion guard passes a stopless trade.
    c = cfg(symbols={"SOLUSDT": {**SOL_STEPPED, "price_step": 1.0}})
    spec, facts, refusal = pe.bracket_from_ticket(
        ticket(entry=120.2, sl=120.1, tp=126.0, qty=1.0), c)
    assert spec is None and "no stop distance to risk" in refusal


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
    def __init__(self, account=None, positions=(), orders=(), after_submit=None, attempt=None, read_error=None,
                 quote=None):
        self.account = account or acct()
        self.quote = quote if quote is not None else {"bid": 119.99, "ask": 120.0}   # inside ticket()'s band
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

    def read_quote(self, page, venue):
        return self.quote or None

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

    def flatten(self, page, symbol=None, *, arm=False, **facts):
        self.calls.append(("flatten", symbol, arm))
        self.flatten_facts = facts
        return {"ok": True, "clicked": arm}

    def modify_bracket(self, page, position, sl, tp, *, arm=False, rollout=None):
        self.calls.append(("modify_bracket", position.symbol, arm))
        self.rollout = rollout
        if getattr(self, "modify_result", None) is not None:
            return self.modify_result
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


def test_not_submitted_attempt_is_retryable_not_confirmed(env):
    # A refusal before any submit click is never confirmed and never written
    # off on the first attempt: it is retry_pending, and nothing is reported.
    ad = FakeAdapter(attempt=PlaceAttempt(stage="refused", detail="one-click trading is on"))
    api = FakeApi([ticket()])
    run(ad, api, env)
    assert env[0].state("prop-manual-aaa") == pe.RETRY_STATE
    assert env[0].latest()["prop-manual-aaa"]["attempts"] == 1
    assert not any(p.get("ticket_id") == "prop-manual-aaa" for p in api.posts)


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

# The live submit STATES the order: "<Side> <qty> <SYM> at <price>" (every
# label measured live, breakout-login-check runs #13942 .. #15537), rewritten
# by the terminal as the quantity / side change. These replicas predate that
# measurement, so each carries this relabel: once a quantity is typed, the
# submit reads "<side> <qty> <sym> at 120.00". The side is the one the label
# already names (a terminal whose label IS the side read-back keeps it),
# else the side picked. ``window.__noAutoLabel`` lets a test own the label.
def _relabel_js(sym_js, side_js):
    return ("<script>(()=>{const L=()=>{if(window.__noAutoLabel)return;const s=document.getElementById('sub'),"
            "q=document.getElementById('q');if(!s||!q||!q.value)return;"
            "const m=s.textContent.match(/\\b(Buy|Sell)\\b/i);const sd=m?m[1]:(" + side_js + ");if(!sd)return;"
            "const t=sd[0].toUpperCase()+sd.slice(1).toLowerCase()+' '+parseFloat(q.value)+' '+(" + sym_js + ")+' at 120.00';"
            "if(s.textContent!==t)s.textContent=t;};document.addEventListener('input',L,true);"
            "document.addEventListener('click',()=>setTimeout(L,0),true);setInterval(L,20);})()</script>")


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
TICKET_PAGE = TICKET_PAGE.replace("</body>", _relabel_js(
    "document.querySelector('.hdr').textContent.trim()", "window.__side") + "</body>")


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
    # (this test owns its submit label: "Place Order" names no side)
    html = (TICKET_PAGE % "").replace("window.__side='buy';sel(this)", "window.__side='buy'") \
                             .replace("window.__side='sell';sel(this)", "window.__side='sell'").replace("<body>", "<body><script>window.__noAutoLabel=1</script>", 1)
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
    # Criterion D7 is read back: the dismiss clicked Cancel and the form is gone.
    assert att.form["ticket_after"] == {"dismissed": True, "found": False, "values": {}, "toggles": {}, "selected": None}
    assert pe._attempt_public(att)["ticket_after"]["found"] is False


def test_disarmed_walk_reports_a_sidebar_that_stays_open_after_dismiss(tpage):
    # A sidebar ticket with no Cancel control that ignores Escape: the walk
    # reports what is left (our values, toggles still ON), it does not assume.
    html = (TICKET_PAGE % "").replace(
        '<button onclick="document.getElementById(\'ticket\').style.display=\'none\'">Cancel</button>', '')
    assert "Cancel" not in html
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(tpage(html=html), SOL)
    assert att.stage == "form_verified", att.detail
    after = att.form["ticket_after"]
    assert after["found"] is True and after["dismissed"] is True          # Escape was pressed, nothing closed
    assert after["values"] == {"quantity": "0.5", "price": "120", "stop_loss": "118", "take_profit": "126"}
    assert after["toggles"] == {"stop_loss": True, "take_profit": True}
    assert after["selected"] == {"side": "buy", "order_type": "limit"}


def test_place_bracket_armed_clicks_submit_exactly_once(tpage):
    p = tpage()
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.submitted is True and p.evaluate("window.__submits") == 1


def test_place_bracket_refuses_a_form_for_another_symbol(tpage):
    p = tpage(html=(TICKET_PAGE % "").replace('<div class="hdr">SOLUSD</div>', '<div class="hdr">SOLUSDX</div>'))
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    # Since the per-ticket symbol switch (PROP-ETH-DOM 2026-09-30): a form for
    # another symbol first tries to link the ticket's symbol; this page has no
    # watchlist, so the switch refuses -- still refused, still never submitted.
    assert att.stage == "refused" and att.detail.startswith("symbol switch:")
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

    def flatten(self, page, symbol=None, *, arm=False, **facts):
        self.calls.append(("flatten", symbol, arm))
        self.flatten_facts = facts
        if arm and self.close_works:
            self.positions = []
            if getattr(self, "orphan_after_close", False):
                self.orders = [WorkingOrder(symbol=symbol, side="short", quantity=0.01, price=120.18,
                                            order_id="777", order_type="limit")]
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


def test_dry_walk_cap_sizes_only_the_dry_walk(tmp_path):
    """TRADEIFY-SOL-SIZE: dry_walk_max_lots lets the DRY walk use a realistic
    size; the armed round trip still uses (and is capped by) watched_click_max_lots."""
    c = rt_cfg(dry_walk_max_lots={"SOLUSD": 1.0})
    ad = RTAdapter()
    res = rt(ad, FakeApi(), tmp_path, arm=False, c=c)
    assert res.halted is None, res.actions
    assert ad.calls[0][0] == "place_bracket" and ad.calls[0][2] is False
    spec = [a for a in res.actions if a["what"] == "round_trip_spec"][0]["spec"]
    assert spec["quantity"] == 1.0
    ad2 = RTAdapter()
    res2 = rt(ad2, FakeApi(), tmp_path, arm=True, c=c, lots=1.0)
    assert "must be > 0 and <= watched_click_max_lots 0.1" in (res2.halted or "") and ad2.calls == []


def test_tradeify_1_dry_walk_cap_arms_nothing():
    c = pe.load_config("tradeify_1")
    assert c.watched_click_max_lots == {"ETHUSD": 0.01, "SOLUSD": 0.01}
    assert c.dry_walk_max_lots == {"SOLUSD": 1.0}


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
    # ETHUSD min_lots 0.01 MEASURED by B4 (#15021, no clamp at 0.01 = the lot step); SOLUSD's stays null.
    for s, mn in (("ETHUSDT", 0.01), ("SOLUSDT", None)):
        assert (c.symbols[s]["lot_units"], c.symbols[s]["lot_step"], c.symbols[s]["min_lots"]) == (1, 0.01, mn)


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
LIVE_SIDEBAR = LIVE_SIDEBAR.replace("</body>", _relabel_js(
    "document.querySelector('.hdr').textContent.trim()",
    "(document.querySelector('[data-g=side][aria-pressed=true]')||{}).textContent") + "</body>")
SUBMIT_BTN = '<button id="sub" onclick="window.__submits=(window.__submits||0)+1">Place Order</button>'


def _live(slmode="Price", submit=SUBMIT_BTN, extra=""):
    return LIVE_SIDEBAR.replace("%SLMODE%", slmode).replace("%SUBMIT%", submit) + extra


def test_live_shape_switches_both_toggles_on_scrolls_to_submit_and_verifies(tpage):
    p = tpage(html=_live())
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL)
    assert att.stage == "form_verified", att.detail
    assert p.evaluate("document.getElementById('slt').dataset.value") == "true"
    assert p.evaluate("document.getElementById('tpt').dataset.value") == "true"
    assert att.form["submit"]["scrolled"] is True and att.form["submit"]["text"] == "Buy 0.5 SOLUSD at 120.00"
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
            " window.__noAutoLabel = 1; document.getElementById('q').value = '9'; });</script>")
    p = tpage(html=_live() + evil)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "read-back after scrolling to submit" in att.detail
    assert p.evaluate("window.__submits") is None


def test_a_submit_that_does_not_name_the_side_refuses(tpage):
    # (this test owns its submit label: "Place Order" names no side)
    p = tpage(html=_live().replace("b.textContent+' SOLUSD'", "'Place Order'")
              .replace("<script>", "<script>window.__noAutoLabel=1;", 1))
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


class JournalDownApi(FakeApi):
    """open_fills raises URLError-like for the first ``down`` calls (an
    ict-web-api restart by the deploy, 2026-09-30 16:05:05-07Z)."""
    def __init__(self, *a, down=1, **kw):
        super().__init__(*a, **kw)
        self.down = down

    def open_fills(self, account_id):
        if self.down > 0:
            self.down -= 1
            raise OSError("[Errno 111] Connection refused")
        return super().open_fills(account_id)


def test_one_journal_read_failure_blocks_entries_but_does_not_latch(env):
    # PI-20260930-KMYJ5XC7-0011: one refused journal read latched the executor
    ledger, state = env
    ad = FakeAdapter()
    res = run(ad, JournalDownApi([ticket()], down=1), env)
    assert state.halted() is None
    assert res.halted and "journal read failed" in res.halted
    assert any("journal read failed" in a and "no entries this cycle" in a for a in res.alerts)
    assert not any(c[0] == "place_bracket" for c in ad.calls)
    assert ledger.latest() == {}                       # the ticket is not refused: next cycle takes it in
    res = run(ad, JournalDownApi([ticket()], down=0), env)
    assert state.halted() is None and not (res.halted or "").startswith("journal read failed")


def test_two_consecutive_journal_read_failures_latch(env):
    _, state = env
    run(FakeAdapter(), JournalDownApi(down=1), env)
    assert state.halted() is None
    res = run(FakeAdapter(), JournalDownApi(down=1), env)
    assert state.halted() and "2 consecutive ticks" in state.halted() and "journal read failed" in state.halted()
    assert any(a.startswith("AUTO-REVERT") for a in res.alerts)


def test_a_terminal_error_then_a_journal_error_share_the_count(env):
    _, state = env
    run(FakeAdapter(read_error="x"), FakeApi(), env)
    run(FakeAdapter(), JournalDownApi(down=1), env)
    assert state.halted() and "2 consecutive ticks" in state.halted()


def test_a_clean_tick_resets_the_journal_error_count(env):
    _, state = env
    run(FakeAdapter(), JournalDownApi(down=1), env)
    run(FakeAdapter(), FakeApi(), env)
    run(FakeAdapter(), JournalDownApi(down=1), env)
    assert state.halted() is None


def test_a_failed_journal_read_never_counts_as_flat(env):
    # A journal-open fill with the terminal flat is reported closed only after
    # two CLEAN reads; a failed read in between is not one of them.
    fills = [{"id": 1, "account_id": "breakout_1", "symbol": "SOLUSDT", "direction": "long",
              "status": "open", "ticket_id": "tx", "sl": 118, "tp": 126}]
    api = JournalDownApi([], fills, down=1)
    run(FakeAdapter(), api, env)                        # failed read
    run(FakeAdapter(), api, env)                        # first clean read
    assert not [p for p in api.posts if p.get("status") == "closed"]
    run(FakeAdapter(), api, env)                        # second clean read
    assert [p["ticket_id"] for p in api.posts if p.get("status") == "closed"] == ["tx"]


def test_journal_read_failures_never_write_the_latch_in_read_only(env):
    _, state = env
    for _ in range(3):
        run(FakeAdapter(), JournalDownApi(down=1), env, mode="read_only")
    assert state.halted() is None


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
MEASURED_SIDEBAR = MEASURED_SIDEBAR.replace("</body>", _relabel_js(
    "'%SYM%'", "(document.querySelector('.sd[style*=background]')||{}).textContent") + "</body>")


def _measured(sym="SOLUSD", symval="SOLUSD", slmode="Price"):
    return MEASURED_SIDEBAR.replace("%SYMVAL%", symval).replace("%SYM%", sym).replace("%SLMODE%", slmode)


def _live_layout():
    """The LIVE geometry (probe #13855): the fields are scroll content
    (630px) inside a shorter scroll viewport, and the submit is a footer
    OUTSIDE the viewport, below it but above the content box's own bottom."""
    html = _measured().replace(
        '<div id="col" style="position:absolute;left:903px;top:0;width:330px;height:300px;overflow-y:auto">\n <div id="panel">',
        '<div id="side" style="position:absolute;left:903px;top:105px;width:330px">'
        '<div id="col" style="height:300px;overflow-y:scroll">\n <div id="panel" style="min-height:630px">')
    html = html.replace('\n <div id="footer" style="padding-top:200px">', '</div>\n <div id="footer">')
    assert 'id="side"' in html and '<div id="footer">' in html
    return html


def test_live_layout_finds_the_footer_submit_below_the_scroll_viewport(tpage):
    p = tpage(html=_live_layout())
    form = DXtradeAdapter(timeout_ms=3_000)._find_form(p)
    assert form["buttons"].get("submit") == "Buy SOLUSD" and form["submit_outside_form"] is True
    spec = BracketSpec("t7", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec)
    assert att.stage == "form_verified", att.detail
    assert p.evaluate("window.__submits") is None


def test_live_layout_armed_clicks_the_footer_submit_once(tpage):
    p = tpage(html=_live_layout())
    spec = BracketSpec("t8", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec, arm=True)
    assert att.submitted, att.detail
    assert p.evaluate("window.__submits") == 1


def test_deeply_nested_footer_submit_is_found_document_wide(tpage):
    # The footer sits 6 ancestors above the fields (more than the old
    # 4-ancestor walk reached); the document-wide search in the form's
    # column still finds exactly it.
    html = _live_layout().replace('<div id="col"', '<div><div><div><div><div id="col"', 1) \
                         .replace('</div>\n <div id="footer">', '</div></div></div></div></div>\n <div id="footer">', 1)
    p = tpage(html=html)
    form = DXtradeAdapter(timeout_ms=3_000)._find_form(p)
    assert form["buttons"].get("submit") == "Buy SOLUSD", form.get("submit_search")


def test_submit_search_reports_why_a_candidate_failed(tpage):
    # A submit-shaped button OUTSIDE the column: refused, and the log says why.
    html = _live_layout().replace('<div id="footer">', '<div id="footer" style="position:absolute;left:-600px;top:500px">', 1)
    p = tpage(html=html)
    spec = BracketSpec("t9", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec, arm=True)
    assert att.stage == "refused" and "no unique submit" in att.detail
    pub = pe._attempt_public(att)
    m = pub["submit_search"]["matches"]
    assert m and m[0]["text"] == "Buy SOLUSD" and m[0]["in_column"] is False and not m[0]["in_form"]
    assert p.evaluate("window.__submits") is None


def test_tick_start_line_carries_the_code_sha():
    import importlib.util
    spec = importlib.util.spec_from_file_location("tick", "scripts/prop/prop_executor_tick.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sha = mod._code_sha()
    assert sha and (sha == "unknown" or len(sha) >= 7)


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
    assert att.form["submit"]["scrolled"] is True and att.form["submit"]["text"] == "Buy 0.01 SOLUSD at 120.00"
    assert p.evaluate("window.__submits") is None


def test_measured_sidebar_sell_is_selected_and_read_back_by_colour(tpage):
    p = tpage(html=_measured())
    spec = BracketSpec("t2", "SOLUSD", "short", 0.01, 126.0, 118.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec)
    assert att.stage == "form_verified", att.detail
    assert att.form["selected"]["side"] == "sell" and att.form["submit"]["text"] == "Sell 0.01 SOLUSD at 120.00"


def test_measured_sidebar_refuses_another_symbol(tpage):
    p = tpage(html=_measured(sym="ETHUSD", symval="ETHUSD"))
    spec = BracketSpec("t3", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec, arm=True)
    # the per-ticket switch refuses first (no watchlist on this page); never submitted
    assert att.stage == "refused" and att.detail.startswith("symbol switch:")
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
    js = ("<script>window.__noAutoLabel=1;function relabel(){const b=document.querySelector('.sd[style*=background]');"
          "let q=parseFloat(document.getElementById('q').value)||0;"
          + (f"if(q>0&&q<{clamp_min})q={clamp_min};" if clamp_min else "") +
          "document.getElementById('sub').textContent=(b?b.textContent.trim():'Buy')+' '+q+' SOLUSD at 120.05'}"
          "document.addEventListener('input',relabel);document.addEventListener('click',()=>setTimeout(relabel,0));</script>")
    return _measured().replace("</body>", js + "</body>")


def test_every_live_measured_submit_label_still_passes():
    # Every submit label the LIVE terminal showed (breakout-login-check runs
    # #13942, #13953, #13965, #13983, #13987, #14154, #14191, #14330, #14344,
    # #14761, #15021, #15261, #15263, #15464, #15537 -- 16 distinct labels;
    # plus the 2026-10-02 live ETH short): the tightening refuses none of them.
    from types import SimpleNamespace
    from src.prop.platform.dxtrade import submit_label_mismatch
    live = [("Buy 0.01 SOLUSD at 117.49", 0.01, "SOLUSD"), ("Buy 0.01 SOLUSD at 118.02", 0.01, "SOLUSD"),
            ("Buy 0.01 SOLUSD at 118.25", 0.01, "SOLUSD"), ("Buy 0.01 SOLUSD at 118.52", 0.01, "SOLUSD"),
            ("Buy 0.01 SOLUSD at 118.94", 0.01, "SOLUSD"), ("Buy 0.01 SOLUSD at 119.03", 0.01, "SOLUSD"),
            ("Buy 0.01 SOLUSD at 119.04", 0.01, "SOLUSD"), ("Buy 0.01 SOLUSD at 120.09", 0.01, "SOLUSD"),
            ("Buy 0.01 SOLUSD at 120.59", 0.01, "SOLUSD"), ("Buy 0.01 SOLUSD at 120.73", 0.01, "SOLUSD"),
            ("Buy 49 SOLUSD at 117.52", 49, "SOLUSD"), ("Buy 49 SOLUSD at 117.77", 49, "SOLUSD"),
            ("Buy 0.01 ETHUSD at 2,697.81", 0.01, "ETHUSD"), ("Buy 0.01 ETHUSD at 2,698.50", 0.01, "ETHUSD"),
            ("Buy 0.01 ETHUSD at 2,723.74", 0.01, "ETHUSD"), ("Buy 0.01 ETHUSD at 2,749.46", 0.01, "ETHUSD"),
            ("Sell 1.22 ETHUSD at 2,657.11", 1.22, "ETHUSD"), ("Buy 0.01 ETH/USD at 2,685.84", 0.01, "ETHUSD")]
    for label, qty, sym in live:
        assert submit_label_mismatch(label, SimpleNamespace(quantity=qty, venue_symbol=sym)) == "", label


def test_submit_label_mismatch_reads_the_measured_label_shape():
    from src.prop.platform.dxtrade import submit_label_mismatch
    spec = BracketSpec("t", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    assert submit_label_mismatch("Buy 0.01 SOLUSD at 120.05", spec) == ""
    # A label that does not state "<Buy|Sell> <qty> <SYM>" is refused
    # (DIALOG-MEASURE): a modify-mode surface's "Save" / "Modify" is no order.
    for label in ("Buy SOLUSD", "Save", "Modify", "Modify Position", "Place Order", ""):
        assert "does not read as" in submit_label_mismatch(label, spec), label
    assert "states quantity 0.1" in submit_label_mismatch("Buy 0.1 SOLUSD at 120.05", spec)
    assert "names 'ETHUSD'" in submit_label_mismatch("Buy 0.01 ETHUSD at 3000.1", spec)


def test_submit_label_mismatch_refuses_the_opposite_side():
    # TRADEIFY-DRY 2026-10-03: the label's own Buy/Sell must match the spec's
    # side; before, "Sell 0.01 ETH/USD" passed a long spec.
    from src.prop.platform.dxtrade import submit_label_mismatch
    long_eth = BracketSpec("t", "ETHUSD", "long", 0.01, 2600.0, 2800.0, "limit", 2680.0)
    short_eth = BracketSpec("t", "ETHUSD", "short", 0.01, 2800.0, 2600.0, "limit", 2680.0)
    assert submit_label_mismatch("Buy 0.01 ETH/USD at 2,678.650", long_eth) == ""
    assert submit_label_mismatch("Sell 0.01 ETH/USD at 2,678.650", short_eth) == ""
    assert "states side 'Sell'" in submit_label_mismatch("Sell 0.01 ETH/USD at 2,678.650", long_eth)
    assert "states side 'Buy'" in submit_label_mismatch("Buy 0.01 ETH/USD at 2,678.650", short_eth)


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


def test_stuck_toggle_is_named_before_any_fill(tpage):
    p = tpage(html=_measured().replace('id="tpt" data-value="false"', 'id="tpt" data-value="false" data-stuck="1"'))
    spec = BracketSpec("t10", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec, arm=True)
    assert att.stage == "refused" and "did not enable ['take_profit']" in att.detail, att.detail
    assert p.evaluate("window.__submits") is None


def test_a_fill_timeout_names_the_step_and_the_reason(tpage):
    # A quantity input that never becomes editable: the refusal says WHICH
    # step timed out and Playwright's reason, never a bare "TimeoutError".
    p = tpage(html=_measured().replace('<input id="q" inputmode="numeric"', '<input id="q" readonly inputmode="numeric"'))
    spec = BracketSpec("t11", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec, arm=True)
    assert att.stage == "refused" and "at quantity fill (TimeoutError:" in att.detail, att.detail
    assert p.evaluate("window.__submits") is None


def test_already_selected_side_is_not_clicked(tpage):
    # Dry run #13953: the click on the ALREADY-selected BUY timed out. An
    # overlay that swallows clicks on BUY must not stop the walk when BUY is
    # already shown selected (the read-back still verifies it).
    btn = '<button class="tb sd" data-test-id="BUY" onclick="pickSide(this)" style="background:rgb(26, 143, 109)"><span>Buy</span></button>'
    html = _measured().replace(btn, '<div style="position:relative;display:inline-block">' + btn +
                               '<div style="position:absolute;left:0;top:0;right:0;bottom:0;z-index:9"></div></div>')
    assert html != _measured()
    p = tpage(html=html)
    spec = BracketSpec("t12", "SOLUSD", "long", 0.01, 118.0, 126.0, "market", None)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, spec)
    assert att.stage == "form_verified", att.detail
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
    # The ticket is NOT the executor's: no write-back, so it stays `emitted`
    # for the manual bridge (invalidation warning, expiry prompt, reticket guard).
    assert not any(p.get("ticket_id") == "prop-eth" for p in api.posts)
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


def test_the_real_config_enables_sol_and_eth():
    # PROP-ETH-DOM B5 (2026-10-01): ETHUSD joins with every spec measured.
    c = pe.load_config("breakout_1")
    assert c.enabled_venue_symbols == ["ETHUSD", "SOLUSD"]
    eth = c.symbols["ETHUSDT"]
    assert eth == {"venue": "ETHUSD", "cvpp": 1.0, "lot_units": 1, "min_lots": 0.01, "lot_step": 0.01,
                   "price_step": 0.01}
    assert c.watched_click_max_lots["ETHUSD"] == 0.01 and c.risk_cap_usd == 75.0


def _eth_not_enabled():
    c = _sol_only()
    c.symbols = {**c.symbols, "ETHUSDT": {"venue": "ETHUSD", "cvpp": 1.0, "lot_units": 1.0,
                                          "min_lots": 0.01, "lot_step": 0.01}}
    c.watched_click_max_lots = {"ETHUSD": 0.01, "SOLUSD": 0.01}
    return c


def test_an_armed_round_trip_refuses_a_non_enabled_venue_before_any_read(env):
    ledger, _ = env
    ad = FakeAdapter()
    res = pe.run_round_trip(adapter=ad, page=None, api=FakeApi(), cfg=_eth_not_enabled(), ledger=ledger,
                            venue_symbol="ETHUSD", arm=True)
    assert res.halted and "enabled_venue_symbols" in res.halted
    assert ad.calls == []


def test_a_dry_round_trip_walks_a_non_enabled_venue_and_submits_nothing(env):
    """The dry walk is the D1-D7 measurement a symbol passes BEFORE it is enabled (PROP-ETH-DOM B4)."""
    ledger, _ = env
    ad = FakeAdapter()
    res = pe.run_round_trip(adapter=ad, page=None, api=FakeApi(), cfg=_eth_not_enabled(), ledger=ledger,
                            venue_symbol="ETHUSD", arm=False)
    assert not (res.halted and "enabled_venue_symbols" in res.halted)
    assert [c for c in ad.calls if c[0] == "place_bracket"], ad.calls
    assert all(c[2] is False for c in ad.calls if c[0] in ("place_bracket", "flatten"))


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


# ── PROP-EXEC fix-forward #7 (dry run #13965): tick-rounded prices, a settle
# after the quantity, re-fill of a field the terminal reset, and no control
# outside the ticket panel ────────────────────────────────────────────────


def test_round_to_step_rounds_to_the_venue_increment_or_leaves_it():
    assert pe.round_to_step(116.8398, 0.01) == 116.84
    assert pe.round_to_step(119.2002, 0.01) == 119.2
    assert pe.round_to_step(118.0, 0.01) == 118.0
    assert pe.round_to_step(116.8398, None) == 116.8398
    assert pe.round_to_step(116.8398, 0) == 116.8398
    assert pe.round_to_step(None, 0.01) is None


def test_verify_form_values_accepts_a_price_within_one_tick_only_when_told():
    from src.prop.platform.dxtrade import price_tolerances
    tol = {"take_profit": 0.01, "stop_loss": 0.01}
    # the measured #13965 read-back: the terminal rounded 119.2002 to 119.2
    assert verify_form_values({"take_profit": {"value": "119.2"}}, {"take_profit": 119.2002}, abs_tol=tol) == []
    # ... and reset the stop loss to its own default: more than a tick away
    bad = verify_form_values({"stop_loss": {"value": "117.96"}}, {"stop_loss": 116.84}, abs_tol=tol)
    assert bad == ["stop_loss: typed 116.84, form shows 117.96 (more than one tick of 0.01 away)"]
    # exactly one tick passes, no tolerance keeps the exact rule, quantity never gets one
    assert verify_form_values({"stop_loss": {"value": "116.85"}}, {"stop_loss": 116.84}, abs_tol=tol) == []
    assert verify_form_values({"take_profit": {"value": "119.2"}}, {"take_profit": 119.2002}) == [
        "take_profit: typed 119.2002, form shows 119.2"]
    spec = BracketSpec("t", "SOLUSD", "long", 0.01, 116.84, 119.2, "market", price_step=0.01)
    assert price_tolerances(spec, {"quantity": 0.01, "stop_loss": 116.84, "take_profit": 119.2}) == {
        "stop_loss": 0.01, "take_profit": 0.01}
    assert price_tolerances(SOL, {"quantity": 0.5, "stop_loss": 118.0}) == {}


def test_round_trip_types_tick_rounded_brackets(tmp_path):
    ad = RTAdapter(quote={"bid": 118.01, "ask": 118.02})
    c = cfg(symbols={"SOLUSDT": {"venue": "SOLUSD", "cvpp": 1.0, "lot_units": 1.0, "min_lots": 0.01,
                                 "lot_step": 0.01, "price_step": 0.01}},
            watched_click_max_lots={"SOLUSD": 0.01})
    res = rt(ad, FakeApi(), tmp_path, arm=False, c=c)
    spec = next(a for a in res.actions if a["what"] == "round_trip_spec")["spec"]
    assert (spec["stop_loss"], spec["take_profit"], spec["price_step"]) == (116.84, 119.2, 0.01)
    # no declared step: the raw values, exact read-back required
    res = rt(RTAdapter(quote={"bid": 118.01, "ask": 118.02}), FakeApi(), tmp_path, arm=False)
    spec = next(a for a in res.actions if a["what"] == "round_trip_spec")["spec"]
    assert (spec["stop_loss"], spec["take_profit"], spec["price_step"]) == (116.8398, 119.2002, None)


def test_bracket_from_ticket_types_tick_rounded_prices_and_grades_the_typed_risk():
    c = cfg(symbols={"SOLUSDT": {"venue": "SOLUSD", "cvpp": 1.0, "lot_units": 1.0, "min_lots": 0.01,
                                 "lot_step": 0.01, "price_step": 0.01}})
    spec, facts, why = pe.bracket_from_ticket(ticket(entry=120.004, sl=118.006, tp=126.001, qty=1), c)
    assert why == "" and (spec.limit_price, spec.stop_loss, spec.take_profit, spec.price_step) == (120.0, 118.01, 126.0, 0.01)
    assert facts["ticket_risk_usd"] == round(1.0 * (120.0 - 118.01) * 1.0, 2)


def test_real_config_sol_and_eth_price_steps_are_measured():
    c = pe.load_config("breakout_1")
    assert c.symbols["SOLUSDT"]["price_step"] == 0.01
    assert c.symbols["ETHUSDT"]["price_step"] == 0.01   # B3, symbol-switch-dry #14999


def test_tick_script_opens_the_browser_at_the_operator_sized_viewport():
    from scripts.prop import prop_executor_tick as tick
    assert tick.VIEWPORT == {"width": 1920, "height": 1600}
    src = (Path(__file__).resolve().parents[1] / "scripts" / "prop" / "prop_executor_tick.py").read_text()
    assert src.count("browser.new_context(") == 2 and src.count("viewport=VIEWPORT") == 2


def test_verify_buttons_in_panel():
    from src.prop.platform.dxtrade import verify_buttons_in_panel
    form = {"panel_box": [903, 99, 330, 630], "fields_box": [903, 99, 330, 630],
            "button_boxes": {"side_buy": [1070, 150, 80, 30], "type_market": [910, 120, 60, 24],
                             "submit": [919, 659, 298, 48]}}
    assert verify_buttons_in_panel(form) == []
    # the chart toolbar's own price button, tagged as the side by mistake
    form["button_boxes"]["side_sell"] = [302, 110, 77, 24]
    assert verify_buttons_in_panel(form) == [
        "side_sell: at [302, 110, 77, 24] is outside the ticket panel (fields column [903, 99, 330, 630])"]
    # scrolled above the panel's top stays inside (the sidebar scrolls internally);
    # a zero-size box is outside; no geometry = not judged here
    assert verify_buttons_in_panel({**form, "button_boxes": {"side_buy": [1000, -334, 80, 30]}}) == []
    assert verify_buttons_in_panel({**form, "button_boxes": {"side_buy": [1000, 150, 0, 0]}})
    assert verify_buttons_in_panel({"button_boxes": {"side_buy": [1, 1, 5, 5]}}) == []


def test_place_bracket_refills_a_field_the_terminal_resets_after_typing(tpage):
    # The #13965 shape: the stop-loss field takes our value, then the
    # terminal's async default recompute overwrites it with 117.96 once. The
    # adapter re-reads, re-fills the one field, and verifies again.
    html = (TICKET_PAGE % "").replace(
        '<input id="sl" aria-label="Stop Loss price" disabled>',
        '<input id="sl" aria-label="Stop Loss price" disabled oninput="if(!window.__reset){window.__reset=1;'
        'setTimeout(()=>{this.value=\'117.96\'},50)}">')
    p = tpage(html=html)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL)
    assert att.stage == "form_verified", att.detail
    assert p.evaluate("document.getElementById('sl').value") == "118"
    trace = att.form["fill_trace"]
    assert [t["refilled"] for t in trace] == [["stop_loss"], []]
    assert trace[0]["shown"]["stop_loss"] == "117.96" and trace[1]["shown"]["stop_loss"] == "118"
    pub = pe._attempt_public(att)
    assert pub["fill_trace"] == trace and pub["panel"]["fields_box"] and pub["panel"]["button_boxes"]["submit"]


def test_place_bracket_refuses_a_field_the_terminal_keeps_resetting(tpage):
    html = (TICKET_PAGE % "").replace(
        '<input id="sl" aria-label="Stop Loss price" disabled>',
        '<input id="sl" aria-label="Stop Loss price" disabled oninput="setTimeout(()=>{this.value=\'117.96\'},50)">')
    p = tpage(html=html)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and "stop_loss: typed 118, form shows 117.96" in att.detail
    assert [t["refilled"] for t in att.form["fill_trace"]] == [["stop_loss"]] * 3
    assert p.evaluate("window.__submits") is None


def test_place_bracket_read_back_accepts_the_terminal_rounding_to_its_tick(tpage):
    # the ticket rounds prices to 2 decimals (measured #13965: 119.2002 -> 119.2)
    rounding = 'oninput="this.value=String(Math.round(+this.value*100)/100)"'
    html = (TICKET_PAGE % "").replace('<input id="sl" aria-label="Stop Loss price" disabled>',
                                      f'<input id="sl" aria-label="Stop Loss price" disabled {rounding}>') \
                             .replace('<input id="tp" aria-label="Take Profit price" disabled>',
                                      f'<input id="tp" aria-label="Take Profit price" disabled {rounding}>')
    ticked = BracketSpec("t1", "SOLUSD", "long", 0.5, 118.004, 126.001, "limit", 120.0, price_step=0.01)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(tpage(html=html), ticked)
    assert att.stage == "form_verified", att.detail
    exact = BracketSpec("t1", "SOLUSD", "long", 0.5, 118.004, 126.001, "limit", 120.0)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(tpage(html=html), exact)
    assert att.stage == "refused" and "stop_loss: typed 118.004, form shows 118" in att.detail


def test_place_bracket_refuses_a_side_button_outside_the_ticket_panel(tpage):
    # the tagged Buy sits where the chart toolbar is, far left of the ticket column
    html = (TICKET_PAGE % "").replace('<div id="ticket" style="display:none">',
                                      '<div id="ticket" style="display:none;margin-left:600px">') \
                             .replace('<button data-g="side" onclick="window.__side=\'buy\';sel(this)">Buy</button>',
                                      '<button data-g="side" style="position:absolute;left:0;top:0" '
                                      'onclick="window.__side=\'buy\';sel(this)">Buy</button>')
    p = tpage(html=html)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and att.detail.startswith("control outside the ticket panel: side_buy: at ["), att.detail
    assert p.evaluate("window.__submits") is None and p.evaluate("window.__side") is None


# ── after the ARMED submit click: what appeared is RECORDED, never pressed
# (live test #13987: the submit alone filled the order; the log said nothing
# about the page after the click) ────────────────────────────────────────

_MODAL = """
<div id="modal" style="display:none;position:fixed;left:300px;top:300px;background:#fff">
  <div>Order placed: Buy 0.01 SOLUSD at 118.94 (position 12345678)</div>
  <button id="mc" onclick="window.__pressed=(window.__pressed||0)+1;document.getElementById('modal').style.display='none'">%s</button>
  <button onclick="document.getElementById('modal').style.display='none'">Cancel</button>
</div>
"""


def _modal_page(label="OK", submit_opens_modal=True):
    html = TICKET_PAGE % ""
    sub = '<button id="sub" onclick="window.__submits=(window.__submits||0)+1">Place Order</button>'
    assert sub in html
    if submit_opens_modal:
        html = html.replace(sub, '<button id="sub" onclick="window.__submits=(window.__submits||0)+1;'
                                 'document.getElementById(\'modal\').style.display=\'block\'">Place Order</button>')
    return html.replace("</body>", (_MODAL % label) + "</body>")


@pytest.mark.parametrize("label", ["OK", "Confirm", "Buy 0.5 SOLUSD at 120.0", "Sell"])
def test_armed_submit_records_what_appeared_and_presses_nothing(tpage, label):
    p = tpage(html=_modal_page(label))
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.submitted is True and p.evaluate("window.__submits") == 1
    assert p.evaluate("window.__pressed") is None                      # never a second click
    after = att.form["after_submit"]
    assert after["dialog_confirmed"] is False and after["why"] == "recorded only; nothing pressed"
    assert [b["text"] for b in after["new_buttons"]] == [label, "Cancel"]
    assert "Buy 0.01 SOLUSD at 118.94" in after["overlay_text"] and "12345678" not in after["overlay_text"]
    pub = pe._attempt_public(att)
    assert pub["after_submit"] == after and "12345678" not in json.dumps(pub)


def test_armed_submit_with_nothing_new_records_an_empty_diff(tpage):
    p = tpage(html=_modal_page(submit_opens_modal=False))
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    after = att.form["after_submit"]
    assert after["new_buttons"] == [] and after["overlay_text"] is None and after["dialogs"] == 0


# ── the positions reader must be MEASURABLE against a live position ──────


def test_dump_tables_lists_every_table_its_classification_and_masks_ids(tpage):
    html = (TICKET_PAGE % "").replace("</body>", """
<div class="tabs"><div data-active="true">Positions</div><div data-active="false">Orders</div></div>
<table><thead><tr><th>Symbol</th><th>Side</th><th>Position Volume</th><th>Open Price</th><th>Position ID</th></tr></thead>
<tbody><tr><td>SOLUSD</td><td>Buy</td><td>0.01</td><td>118.94</td><td>987654321</td></tr></tbody></table>
<table><thead><tr><th>Symbol</th><th>Side</th><th>Size</th><th>Price</th><th>Order ID</th></tr></thead><tbody></tbody></table>
</body>""")
    lines = DXtradeAdapter(timeout_ms=3_000).dump_tables(tpage(html=html), ("secret-user",))
    assert lines[0].startswith("dump_tables: BEGIN") and lines[-1] == "dump_tables: END"
    assert "dump_tables.tab_like: ['Positions', 'Orders']" in lines
    assert "dump_tables[Positions]: tab_click=True tables=3" in lines
    assert any("reads_as=neither" in ln and "'Bid'" in ln for ln in lines)          # the watchlist
    assert any("reads_as=positions rows=1" in ln and "'Position Volume'" in ln for ln in lines)
    assert any("reads_as=orders rows=0" in ln and "'Order ID'" in ln for ln in lines)
    row = next(ln for ln in lines if ln.startswith("dump_tables.row[") and "'Buy'" in ln)
    assert "'SOLUSD', 'Buy', '0.01', '118.94', '#####'" in row and "987654321" not in "\n".join(lines)


def test_login_check_passes_dump_tables_through():
    src = (Path(__file__).resolve().parents[1] / "scripts" / "prop" / "breakout_login_check.py").read_text()
    assert '"--dump-tables"' in src and "adapter.dump_tables(page, (username, password))" in src
    sh = (Path(__file__).resolve().parents[1] / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    assert '*",dump-tables,"*) WANT_TABLES=1' in sh and 'ARGS+=(--dump-tables)' in sh


# ── the round trip waits the 60 s criterion L2 registers, not 15 s ───────


class LateFillAdapter(RTAdapter):
    """The terminal shows the position only from the N-th read after submit."""

    def __init__(self, late, **kw):
        super().__init__(**kw)
        self.late, self.reads_after_submit, self.submitted = late, 0, False

    def place_bracket(self, page, spec, *, arm=False):
        att = super().place_bracket(page, spec, arm=arm)
        self.submitted = arm
        return att

    def read_positions(self, page):
        if self.submitted and self.positions:
            self.reads_after_submit += 1
            if self.reads_after_submit < self.late:
                return []
        return list(self.positions)


def test_round_trip_confirms_a_fill_that_lands_within_60s(tmp_path):
    ad = LateFillAdapter(late=18)                     # 18 x 3 s = 54 s after submit
    res = rt(ad, FakeApi(), tmp_path)
    assert res.halted is None, res.actions
    assert next(a for a in res.actions if a["what"] == "confirm_entry")["verdict"] == "open"


def test_round_trip_still_stops_when_nothing_lands_within_60s(tmp_path):
    ad = LateFillAdapter(late=30)                     # would land at 90 s
    res = rt(ad, FakeApi(), tmp_path)
    assert res.halted == "round trip stopped at entry (not_found)"
    assert ad.reads_after_submit == 20



# ── the terminal's split tables: a header table with no rows of its own,
# followed by a header-less body table (measured for the watchlist in
# #13898/#13908; the operator's Positions panel, live test #13987) ────────

POSITIONS_HEADERS = ["Symbol", "Side", "Size", "Open P&L", "Take profit", "Stop loss", "Position ID",
                     "Fill Price", "Current Price", "Date and Time"]
POSITION_ROW = ["SOLUSD", "Buy", "0.01", "\u2014", "120.18", "117.80", "987654321", "118.94", "119.48", "29/09/26 09:27"]


def _split_panel(rows, close_html='<button title="Close">\u00d7</button>', headers=None, body=True, cell=False, tr_attrs=""):
    """``cell=True`` is the LIVE layout measured by #14198: two extra empty
    header columns, and the row's controls in their own trailing
    ``<td class="sticky--actions-cell">`` rather than inside the date cell.
    ``tr_attrs`` are extra attributes on each body row (a class, a test id)."""
    headers = list(headers or POSITIONS_HEADERS) + (["", ""] if cell else [])
    head = "<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead><tbody></tbody></table>"
    tail = (lambda r: f"<td>{r[-1]}</td><td></td><td class=\"sticky--actions-cell\">{close_html}</td>") if cell \
        else (lambda r: f"<td>{r[-1]} {close_html}</td>")
    trs = "".join("<tr data-row-id='%d' %s>" % (i, tr_attrs) + "".join(f"<td>{c}</td>" for c in r[:-1])
                  + tail(r) + "</tr>" for i, r in enumerate(rows, 1))
    body_t = f"<table><tbody>{trs}</tbody></table>" if body else ""
    return f'<div class="panel"><div data-active="true">Positions</div>{head}{body_t}</div>'


def _split_page(rows, **kw):
    chart = '<div class="chart"><canvas width="300" height="200"></canvas>' \
            '<button style="position:absolute;left:50px;top:50px" title="Close">\u00d7</button></div>'
    return (TICKET_PAGE % "").replace("</body>", chart + _split_panel(rows, **kw) + "</body>")


def test_positions_reader_pairs_the_header_table_with_its_body_table(tpage):
    p = tpage(html=_split_page([POSITION_ROW]))
    got = DXtradeAdapter(timeout_ms=3_000).read_positions(p)
    assert len(got) == 1
    pos = got[0]
    assert (pos.symbol, pos.side, pos.quantity, pos.entry_price, pos.stop_loss, pos.take_profit) == \
        ("SOLUSD", "long", 0.01, 118.94, 117.8, 120.18)
    assert pos.unrealized_pnl is None                       # the em-dash P&L parses to None, never chokes
    # and the executor's confirmation reads it as an OPEN bracketed position
    spec = {"venue_symbol": "SOLUSD", "side": "long", "quantity": 0.01, "limit_price": None,
            "stop_loss": 117.8, "take_profit": 120.18}
    found = pe.match_terminal(spec, got, [], 0.02)
    assert pe.classify_confirmation(spec, found, 0.02) == "open"


def test_positions_reader_negative_control_an_empty_body_table_is_truly_empty(tpage):
    p = tpage(html=_split_page([]))
    assert DXtradeAdapter(timeout_ms=3_000).read_positions(p) == []
    tables = p.evaluate(__import__("src.prop.platform.dxtrade", fromlist=["EXTRACT_TABLES_JS"]).EXTRACT_TABLES_JS)
    pos_t = next(t for t in tables if "Position ID" in t["headers"])
    assert pos_t["paired"] is True and pos_t["own_rows"] == 0 and pos_t["rows"] == []


def test_positions_reader_does_not_pair_a_body_with_a_different_column_count(tpage):
    # a following header-less table whose rows have 3 cells is NOT the body of a 10-column header
    html = _split_page([]).replace("</tbody></table></div>", "</tbody></table>"
                                    "<table><tbody><tr><td>x</td><td>y</td><td>z</td></tr></tbody></table></div>")
    p = tpage(html=html)
    assert DXtradeAdapter(timeout_ms=3_000).read_positions(p) == []
    tables = p.evaluate(__import__("src.prop.platform.dxtrade", fromlist=["EXTRACT_TABLES_JS"]).EXTRACT_TABLES_JS)
    pos_t = next(t for t in tables if "Position ID" in t["headers"])
    assert pos_t["paired"] is True and pos_t["unpaired_body_rows"] == 0   # paired with the EMPTY body that comes first
    html2 = _split_page([], body=False).replace("</tbody></table></div>", "</tbody></table>"
                                                 "<table><tbody><tr><td>x</td><td>y</td><td>z</td></tr></tbody></table></div>")
    tables = tpage(html=html2).evaluate(__import__("src.prop.platform.dxtrade", fromlist=["EXTRACT_TABLES_JS"]).EXTRACT_TABLES_JS)
    pos_t = next(t for t in tables if "Position ID" in t["headers"])
    assert pos_t["paired"] is False and pos_t["unpaired_body_rows"] == 1 and pos_t["rows"] == []


def test_flatten_finds_the_row_control_in_the_paired_body_and_never_the_chart_overlay(tpage):
    p = tpage(html=_split_page([POSITION_ROW]))
    ad = DXtradeAdapter(timeout_ms=3_000)
    got = ad.flatten(p, "SOLUSD", arm=False)
    assert got["ok"] is True and got["clicked"] is False and got["why"].startswith("disarmed"), got
    assert p.evaluate("document.querySelector('[data-metis-row-action]').closest('tr').dataset.rowId") == "1"
    # the same control pulled out of its row (an overlay-like placement) is refused
    p = tpage(html=_split_page([POSITION_ROW], close_html='<button style="position:fixed;left:60px;top:60px" title="Close">\u00d7</button>'))
    got = ad.flatten(p, "SOLUSD", arm=False)
    assert got["ok"] is False and "not boxed inside its row" in got["why"]
    assert p.evaluate("document.querySelector('[data-metis-row-action]')") is None


def test_dump_tables_reports_the_pairing_and_the_row_controls(tpage):
    lines = DXtradeAdapter(timeout_ms=3_000).dump_tables(tpage(html=_split_page([POSITION_ROW])), ())
    pair = next(ln for ln in lines if ".pairing:" in ln and "first_row_controls=['\u00d7']" in ln)
    assert "paired_body=True own_rows=0 unpaired_body_rows=0" in pair
    row = next(ln for ln in lines if "'SOLUSD', 'Buy', '0.01'" in ln)
    assert "'#####'" in row and "987654321" not in "\n".join(lines)


def test_parse_number_em_dash_is_none():
    from src.prop.platform.dxtrade import parse_number
    assert parse_number("\u2014") is None and parse_number("—") is None and parse_number("0.24") == 0.24


# ── the watched close, to the operator's flow (2026-09-29): hover -> the
# row's LAST control, the only close-type one -> the "Close Position" modal
# read back -> Close Position; anything else presses Discard ─────────────

_CLOSE_CSS = "<style>.acts{visibility:hidden} tr:hover .acts{visibility:visible}</style>"


def _close_modal_html(heading="Close SOLUSD Buy Position", lots="0.01", caption="0.01 out of 0.01"):
    return f"""
<div id="cm" style="display:none;position:fixed;left:400px;top:200px;background:#fff;padding:10px">
  <button aria-label="dismiss" onclick="document.getElementById('cm').style.display='none'">×</button>
  <h3>{heading}</h3>
  <div>Fill Price @118.94 · Current Price 119.48 · Open P&L 0.00</div>
  <label>Lots to Close <input id="lots" value="{lots}"></label>
  <div>{caption}</div>
  <button onclick="window.__discard=(window.__discard||0)+1;document.getElementById('cm').style.display='none'">Discard</button>
  <button onclick="window.__closed=(window.__closed||0)+1;document.getElementById('cm').style.display='none';
                   document.querySelector('tr[data-row-id]:not(.instrument)').remove()">Close Position</button>
</div>"""


def _close_page(row=POSITION_ROW, icons=None, modal=None, chart_x=True, cell=False, tr_attrs=""):
    icons = icons if icons is not None else (
        '<button title="Reverse" onclick="window.__reverse=1">⇄</button>'
        '<button title="Modify" onclick="window.__modify=1">✎</button>'
        '<button title="Close" onclick="document.getElementById(\'cm\').style.display=\'block\'">✕</button>')
    modal = modal if modal is not None else _close_modal_html()
    close_html = f'<span class="acts">{icons}</span>'
    chart = ('<div class="chart"><canvas width="300" height="200"></canvas>'
             '<button style="position:absolute;left:50px;top:50px" title="Close" onclick="window.__chart_x=1">✕</button></div>'
             if chart_x else "")
    return (TICKET_PAGE % "").replace("</body>", _CLOSE_CSS + chart
                                      + _split_panel([row], close_html=close_html, cell=cell, tr_attrs=tr_attrs)
                                      + modal + "</body>")


#: The LIVE trio as #14198 measured it: three text-less <button>s, no title,
#: no aria-label, inside the row's own "sticky--actions-cell". What each one
#: is CALLED (the icon's class) is assumed here — the measured facts are the
#: tag, the count and the absence of any label; the class names come from
#: the next dump-tables read (first_row_control_html).
_LIVE_TRIO = ('<button class="btn-icon" onclick="window.__reverse=1"><svg class="icon icon-reverse"><use href="#i-reverse"/></svg></button>'
              '<button class="btn-icon" onclick="window.__modify=1"><svg class="icon icon-edit"><use href="#i-edit"/></svg></button>'
              '<button class="btn-icon" onclick="document.getElementById(\'cm\').style.display=\'block\'">'
              '<svg class="icon icon-close"><use href="#i-close"/></svg></button>')


def _nothing_pressed(p):
    return all(p.evaluate(f"window.{k}") is None for k in ("__reverse", "__modify", "__chart_x", "__closed"))


def test_watched_close_disarmed_locates_the_row_and_its_last_close_control(tpage):
    p = tpage(html=_close_page())
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=False, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is True and got["clicked"] is False and got["why"].startswith("disarmed")
    assert got["row"] == {"side": "Buy", "size": "0.01", "fill": "118.94", "sl": "117.80", "tp": "120.18"}
    assert [c["hint"].split(" ")[0] for c in got["controls"]] == ["⇄", "✎", "✕"] and got["chosen"] == 2
    assert p.evaluate("document.querySelector('[data-metis-row-action]').title") == "Close"
    assert _nothing_pressed(p)


def test_watched_close_armed_reads_the_modal_back_and_presses_close_position_once(tpage):
    p = tpage(html=_close_page())
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is True and got["clicked"] is True and got["why"] == "Close Position confirmed", got
    assert got["modal"]["heading"] == "Close SOLUSD Buy Position" and got["modal"]["lots"] == "0.01"
    assert got["modal"]["caption"] == ["0.01", "0.01"] and got["modal"]["confirm"] == 1
    # the armed SUCCESS carries what the row showed and which control was
    # chosen (live test #14344: the pass had no control markup in its log)
    assert [c["tag"] for c in got["controls"]] == ["button", "button", "button"] and got["chosen"] == 2
    assert all(c["html"].startswith("<button") for c in got["controls"])
    assert p.evaluate("window.__closed") == 1 and p.evaluate("window.__discard") is None
    assert p.evaluate("window.__reverse") is None and p.evaluate("window.__modify") is None and p.evaluate("window.__chart_x") is None
    assert p.evaluate("document.querySelectorAll('tr[data-row-id]:not(.instrument)').length") == 0     # the row is gone
    # the caller's facts are checked from the row too: lots not edited
    assert p.evaluate("document.getElementById('lots').value") == "0.01"


@pytest.mark.parametrize("side,qty,entry,expect", [
    ("short", 0.01, 118.94, "side: row shows 'Buy'"),
    ("long", 0.02, 118.94, "size: row shows '0.01'"),
    ("long", 0.01, 125.0, "fill: row shows '118.94'"),
])
def test_watched_close_refuses_a_row_that_is_not_the_position_to_close(tpage, side, qty, entry, expect):
    p = tpage(html=_close_page())
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side=side, quantity=qty, entry_price=entry)
    assert got["ok"] is False and got["clicked"] is False and expect in got["why"], got
    assert _nothing_pressed(p) and p.evaluate("document.querySelector('[data-metis-row-action]')") is None


def test_watched_close_refuses_when_the_close_control_is_not_the_last_or_not_unique(tpage):
    ad = DXtradeAdapter(timeout_ms=3_000)
    # close first, then reverse and modify: the last control is the modify pencil
    p = tpage(html=_close_page(icons='<button title="Close">✕</button><button title="Reverse">⇄</button>'
                                     '<button title="Modify">✎</button>'))
    got = ad.flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01)
    assert got["ok"] is False and "not the LAST control" in got["why"] and _nothing_pressed(p)
    # two close-type controls ("Close all" is a QUALIFIED close since the review of
    # #14216 and is covered by test_watched_close_refuses_a_qualified_close)
    p = tpage(html=_close_page(icons='<button title="Close">✕</button><button title="Close">x</button>'))
    got = ad.flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01)
    assert got["ok"] is False and "2 close-type controls" in got["why"] and _nothing_pressed(p)
    # no control at all in the row
    p = tpage(html=_close_page(icons=""))
    got = ad.flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01)
    assert got["ok"] is False and "no control" in got["why"] and _nothing_pressed(p)
    # a reverse/modify-looking control never counts as the close, even when last
    p = tpage(html=_close_page(icons='<button title="Close">✕</button><button title="Reverse close">⇄</button>'))
    got = ad.flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01)
    assert got["ok"] is False and "not the LAST control" in got["why"] and _nothing_pressed(p)


@pytest.mark.parametrize("modal_kw,expect", [
    ({"heading": "Close SOLUSD Sell Position"}, "does not name our side (buy)"),
    ({"heading": "Close ETHUSD Buy Position"}, "does not name SOLUSD"),
    ({"lots": "0.005"}, "lots-to-close 0.005 != the full size 0.01"),
    ({"caption": "0.01 out of 0.02"}, "caption total 0.02 != the full size 0.01"),
    ({"caption": "no caption here"}, "caption not readable"),
])
def test_watched_close_discards_a_modal_that_does_not_read_back(tpage, modal_kw, expect):
    p = tpage(html=_close_page(modal=_close_modal_html(**modal_kw)))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and got["clicked"] is True and expect in got["why"], got
    assert "Discard pressed" in got["why"] and p.evaluate("window.__discard") == 1
    assert p.evaluate("window.__closed") is None and p.evaluate("document.getElementById('cm').style.display") == "none"
    assert p.evaluate("document.querySelectorAll('tr[data-row-id]:not(.instrument)').length") == 1     # the row stays


def test_watched_close_descends_into_the_actions_cell_and_picks_the_last_icon(tpage):
    # REGRESSION (live test #14191): the icon trio sits inside the row's own
    # "sticky--actions-cell"; the finder kept that cell as the only control
    # and refused with "0 close-type controls". The cell is a container.
    ad = DXtradeAdapter(timeout_ms=3_000)
    # (a) the operator's titled icons, in the cell
    p = tpage(html=_close_page(cell=True))
    got = ad.flatten(p, "SOLUSD", arm=False, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is True and got["chosen"] == 2, got
    assert [c["hint"].split(" ")[0] for c in got["controls"]] == ["⇄", "✎", "✕"] and all(c["tag"] == "button" for c in got["controls"])
    assert not any("actions-cell" in c["hint"] for c in got["controls"])
    assert p.evaluate("document.querySelector('[data-metis-row-action]').closest('td').className") == "sticky--actions-cell"
    assert _nothing_pressed(p)
    # (b) the LIVE shape: three text-less buttons told apart by their icons' names
    p = tpage(html=_close_page(icons=_LIVE_TRIO, cell=True))
    got = ad.flatten(p, "SOLUSD", arm=False, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is True and got["chosen"] == 2, got
    assert [c["tag"] for c in got["controls"]] == ["button"] * 3
    assert "icon close" in got["controls"][2]["hint"] and "icon-close" in got["controls"][2]["html"]
    assert _nothing_pressed(p)
    # armed: the modal is read back and Close Position pressed exactly once; reverse / modify never
    got = ad.flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is True and got["why"] == "Close Position confirmed", got
    assert p.evaluate("window.__closed") == 1 and p.evaluate("window.__reverse") is None and p.evaluate("window.__modify") is None
    assert p.evaluate("document.querySelectorAll('tr[data-row-id]:not(.instrument)').length") == 0
    # the live shape's names are readable from the armed success log too
    assert got["chosen"] == 2 and [c["tag"] for c in got["controls"]] == ["button"] * 3
    assert "icon-close" in got["controls"][2]["hint"] + got["controls"][2]["html"]


@pytest.mark.parametrize("icons,expect", [
    # three text-less, nameless buttons: nothing says which one closes -> refuse, and say what was seen
    ('<button onclick="window.__reverse=1"></button><button onclick="window.__modify=1"></button>'
     '<button onclick="window.__closed=1"></button>', "0 close-type controls"),
    # a class token "x-small" is not the glyph x
    ('<button class="btn x-small" onclick="window.__closed=1"></button>', "0 close-type controls"),
    # the close icon inside a button that is itself called reverse: reverse wins, never pressed
    ('<button class="btn-reverse" onclick="window.__reverse=1"><svg class="icon icon-close"/></button>', "0 close-type controls"),
    # the close icon is not the LAST control of the row
    ('<button class="b"><svg class="icon icon-close"/></button><button class="b"><svg class="icon icon-edit"/></button>',
     "not the LAST control"),
    # two close-named icons
    ('<button class="b"><svg class="icon icon-close"/></button><button class="b"><svg class="icon icon-close"/></button>',
     "2 close-type controls"),
])
def test_watched_close_refuses_an_actions_cell_that_does_not_name_one_close(tpage, icons, expect):
    p = tpage(html=_close_page(icons=icons, cell=True))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and got["clicked"] is False and expect in got["why"], got
    assert all("html" in c and c["tag"] == "button" for c in got["controls"]), got["controls"]
    assert _nothing_pressed(p) and p.evaluate("document.querySelector('[data-metis-row-action]')") is None


_ICON_TRIO = ('<button class="btn-icon" onclick="window.__reverse=1"><svg class="icon icon-arrows"/></button>'
              '<button class="btn-icon" onclick="window.__modify=1"><svg class="icon icon-pen"/></button>'
              '<button class="btn-icon" onclick="document.getElementById(\'cm\').style.display=\'block\'"><svg class="icon icon-close"/></button>')


@pytest.mark.parametrize("wrap", [
    '<div class="row-icons">%s</div>',          # matches [class*=icon] itself
    '<div title="Actions">%s</div>',            # a [title] wrapper
    '<span class="acts"><div class="row-icons"><div title="Row actions">%s</div></div></span>',   # nested wrappers
])
def test_watched_close_never_presses_a_wrapper_around_the_trio(tpage, wrap):
    # REGRESSION (review of #14216, F1): a wrapper that itself looks
    # interactive (icon class, title) used to survive as the outermost
    # control, read "close" from its descendants, and its CENTRE was the
    # modify button. Any element holding a pressable is a container.
    trio = _ICON_TRIO
    ad = DXtradeAdapter(timeout_ms=3_000)
    p = tpage(html=_close_page(icons=wrap % trio, cell=True))
    got = ad.flatten(p, "SOLUSD", arm=False, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is True and got["chosen"] == 2, got
    assert [c["tag"] for c in got["controls"]] == ["button"] * 3 and all(c["pressable"] for c in got["controls"])
    assert p.evaluate("document.querySelector('[data-metis-row-action]').tagName") == "BUTTON"
    got = ad.flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is True and got["why"] == "Close Position confirmed", got
    assert p.evaluate("window.__closed") == 1
    assert p.evaluate("window.__modify") is None and p.evaluate("window.__reverse") is None


@pytest.mark.parametrize("icons,expect", [
    # F3 (a): the close-named pressable nested in a reverse button — the click would bubble to reverse
    ('<button class="btn-reverse" onclick="window.__reverse=1">'
     '<span role="button" class="icon-close" style="display:inline-block;width:14px;height:14px"></span></button>',
     "nested in another pressable (button)"),
    # F3 (b): a close-named link inside a modify role=button — both handlers would fire
    ('<div role="button" class="btn-modify" onclick="window.__modify=1" style="display:inline-block">'
     '<a class="icon-close" style="display:inline-block;width:14px;height:14px" '
     'onclick="document.getElementById(\'cm\').style.display=\'block\'"></a></div>',
     "nested in another pressable (div)"),
    # a role=button wrapper around the whole trio: the chosen button has a pressable ancestor
    ('<div role="button">' + _ICON_TRIO + '</div>', "nested in another pressable (div)"),
    # a reverse-named (non-pressable) ancestor disqualifies by its own attributes
    ('<div class="reverse-group"><button class="b" onclick="window.__closed=1"><svg class="icon icon-close"/></button></div>',
     "reverse / modify / qualified ancestor"),
    # a qualified ("close all") ancestor likewise
    ('<div title="Close all"><button class="b" onclick="window.__closed=1"><svg class="icon icon-close"/></button></div>',
     "reverse / modify / qualified ancestor"),
])
def test_watched_close_refuses_a_close_nested_in_or_under_another_control(tpage, icons, expect):
    # REGRESSION (review of #14216, F3), ARMED: nothing is pressed, no handler fires
    p = tpage(html=_close_page(icons=icons, cell=True))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and got["clicked"] is False and expect in got["why"], got
    assert _nothing_pressed(p) and p.evaluate("document.querySelector('[data-metis-row-action]')") is None
    assert p.evaluate("document.getElementById('cm').style.display") == "none"     # the modal never opened


_CLOSE_BTN = '<button class="b" onclick="document.getElementById(\'cm\').style.display=\'block\'"><svg class="icon icon-close"/></button>'


@pytest.mark.parametrize("wrap", [
    '<div class="wrap" onclick="window.__reverse=1">%s</div>',                        # [onclick]
    '<div class="wrap" role="menuitem" tabindex="0" onclick="window.__reverse=1">%s</div>',   # role=menuitem + tabindex
    '<span class="wrap" role="link" onclick="window.__reverse=1">%s</span>',           # role=link
    '<div class="wrap" tabindex="0">%s</div>',                                          # a focusable box, no attribute handler
    '<div class="wrap" role="option" onclick="window.__reverse=1">%s</div>',           # role=option
    '<label class="wrap" onclick="window.__reverse=1">%s</label>',                     # label
    '<details open><summary class="wrap" onclick="window.__reverse=1">%s</summary></details>',   # summary
])
def test_watched_close_refuses_a_close_under_any_clickable_ancestor(tpage, wrap):
    # REGRESSION (review of #14216, round 4), ARMED: the click would bubble to
    # a neutral-named wrapper that takes clicks without being a button / link
    p = tpage(html=_close_page(icons=wrap % _CLOSE_BTN, cell=True))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and got["clicked"] is False and "nested in another pressable" in got["why"], got
    assert _nothing_pressed(p) and p.evaluate("document.querySelector('[data-metis-row-action]')") is None
    assert p.evaluate("document.getElementById('cm').style.display") == "none"


@pytest.mark.parametrize("wrap,flag", [
    ('<div class="reverseBtn" data-w>%s</div>', "__reverse"),                  # camelCase, word first
    ('<div class="btnReverse" data-w>%s</div>', "__reverse"),                  # camelCase, word last
    ('<div class="Row_reverseButton__a1b2c" data-w>%s</div>', "__reverse"),    # CSS-module hash
    ('<div class="modifyOrder" data-w>%s</div>', "__modify"),
    ('<span class="editPosition" data-w>%s</span>', "__modify"),
    ('<div class="closeAll" data-w>%s</div>', "__closeall"),                   # a qualified close, camelCase
    ('<div data-action="reverse" data-w>%s</div>', "__reverse"),               # named by data-action only
    ('<div data-testid="reverse-position" data-w>%s</div>', "__reverse"),      # named by data-testid only
    ('<div id="reversePosition" data-w>%s</div>', "__reverse"),                # named by id only
])
def test_watched_close_refuses_a_close_under_a_listener_ancestor_named_in_camelcase(tpage, wrap, flag):
    # REGRESSION (review of #14216, round 5), ARMED: the wrapper has no
    # onclick, tabindex or role — its handler is attached by addEventListener,
    # so only its NAME can give it away, and at the word rule a camelCase name
    # hid the word. The listener is proven live at the end: a direct click on
    # the wrapper fires it, so the replica would have fired had it been pressed.
    html = _close_page(icons=wrap % _CLOSE_BTN, cell=True).replace(
        "</body>", "<script>document.querySelector('[data-w]').addEventListener('click', () => { window.%s = 1; });</script></body>" % flag)
    p = tpage(html=html)
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and got["clicked"] is False and "reverse / modify / qualified ancestor" in got["why"], got
    assert _nothing_pressed(p) and p.evaluate("window.__closeall") is None
    assert p.evaluate("document.querySelector('[data-metis-row-action]')") is None
    assert p.evaluate("document.getElementById('cm').style.display") == "none"
    p.evaluate("document.querySelector('[data-w]').click()")
    assert p.evaluate(f"window.{flag}") == 1                                     # the probe was live


def test_watched_close_refuses_a_close_icon_inside_a_control_named_by_data_action(tpage):
    # the same three attributes are read on the control itself: a button whose
    # only name is data-action="reverse" holding a close icon is not a close
    icons = '<button data-action="reverse" onclick="window.__reverse=1"><svg class="icon icon-close"/></button>'
    p = tpage(html=_close_page(icons=icons, cell=True))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and got["clicked"] is False and "0 close-type controls" in got["why"], got
    assert _nothing_pressed(p)


def test_watched_close_row_named_in_camelcase_keeps_the_word_rule(tpage):
    # the row itself stays on the word rule, now camelCase-aware: "editableRow"
    # is not the word edit (armed: Close Position confirmed), "reverseRow" is
    p = tpage(html=_close_page(icons=_CLOSE_BTN, cell=True, tr_attrs='class="editableRow swappableRow"'))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is True and got["clicked"] is True and got["why"] == "Close Position confirmed", got
    assert p.evaluate("window.__closed") == 1
    p = tpage(html=_close_page(icons=_CLOSE_BTN, cell=True, tr_attrs='class="reverseRow"'))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and "qualified ancestor" in got["why"] and _nothing_pressed(p), got


def test_watched_close_refuses_an_input_button_as_the_close_control(tpage):
    # an <input type=button|submit> can hold no children, so it can never be an
    # ancestor; as the close-named control itself it is not a pressable we press
    icons = '<input type="button" class="icon-close" value="" onclick="window.__closed=1">'
    p = tpage(html=_close_page(icons=icons, cell=True))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    # (the outermost non-container left is the cell holding it, or the input itself: neither is pressed)
    assert got["ok"] is False and got["clicked"] is False and "not a button (" in got["why"], got
    assert _nothing_pressed(p)


def test_watched_close_why_is_masked_and_a_row_class_like_editable_does_not_refuse(tpage):
    ad = DXtradeAdapter(timeout_ms=3_000)
    # the reviewer's probe: a row carrying a test id; a class token "edit" on the row
    # makes it a reverse / modify ancestor, and the refusal must not quote the id
    p = tpage(html=_close_page(icons=_CLOSE_BTN, cell=True, tr_attrs='class="row edit-mode" data-test-id="pos-7788991"'))
    got = ad.flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and got["clicked"] is False and "qualified ancestor" in got["why"], got
    assert "7788991" not in got["why"] and "#####" in got["why"] and _nothing_pressed(p)
    # a row class "editable" / "swappable" is a WORD, not the token edit / swap:
    # the ancestor rule reads by word, so such a row is not refused
    p = tpage(html=_close_page(icons=_CLOSE_BTN, cell=True, tr_attrs='class="editable swappable" data-test-id="pos-7788991"'))
    got = ad.flatten(p, "SOLUSD", arm=False, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is True and got["chosen"] == 0, got
    # a located-row refusal that quotes the terminal is masked as well
    p = tpage(html=_close_page(icons=_CLOSE_BTN, cell=True))
    got = ad.flatten(p, "ETHUSD", arm=True, side="long", quantity=0.01)
    assert got["ok"] is False and "found 0" in got["why"]


def test_watched_close_hint_is_masked_for_the_log(tpage):
    icons = ('<button data-test-id="reverse-1234567890" onclick="window.__reverse=1"></button>'
             '<button name="modify-deadbeefcafe" onclick="window.__modify=1"></button>'
             '<button data-test-id="close-9876543210" onclick="window.__closed=1"></button>')
    p = tpage(html=_close_page(icons=icons, cell=True))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=False, side="long", quantity=0.01, entry_price=118.94)
    blob = " ".join(c["hint"] + " " + c["html"] for c in got["controls"])
    assert "1234567890" not in blob and "9876543210" not in blob and "deadbeefcafe" not in blob
    assert "#####" in blob and _nothing_pressed(p)


def test_watched_close_refuses_a_close_named_wrapper_with_no_pressable_inside(tpage):
    # a close-named box that holds only icons (no button) is never pressed
    icons = '<div class="row-icons"><svg class="icon icon-arrows"/><svg class="icon icon-pen"/><svg class="icon icon-close"/></div>'
    p = tpage(html=_close_page(icons=icons, cell=True))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and got["clicked"] is False, got
    # the outermost non-container left is the cell itself (or the box): never a pressable
    assert "not a button (" in got["why"] or "close-type controls" in got["why"], got["why"]
    assert _nothing_pressed(p) and p.evaluate("document.querySelector('[data-metis-row-action]')") is None


@pytest.mark.parametrize("icons,expect", [
    # close-all beside reverse: the qualified close is not a close (F2)
    ('<button class="b" onclick="window.__reverse=1"><svg class="icon icon-reverse"/></button>'
     '<button class="b" onclick="window.__closed=1"><svg class="icon icon-close-all"/></button>', "0 close-type controls"),
    # a single close-all as the last (only) control
    ('<button class="b" onclick="window.__closed=1"><svg class="icon icon-close-all"/></button>', "0 close-type controls"),
    # a titled "Close all"
    ('<button title="Close all" onclick="window.__closed=1">✕</button>', "0 close-type controls"),
    # any other qualifier right after close
    ('<button class="b" onclick="window.__closed=1"><svg class="icon icon-close-group"/></button>', "0 close-type controls"),
])
def test_watched_close_refuses_a_qualified_close(tpage, icons, expect):
    p = tpage(html=_close_page(icons=icons, cell=True))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and got["clicked"] is False and expect in got["why"], got
    assert _nothing_pressed(p) and p.evaluate("document.querySelector('[data-metis-row-action]')") is None


def test_watched_close_still_accepts_a_close_position_title_or_icon(tpage):
    # "Close position" (the terminal's likely tooltip) is not a qualified close
    icons = ('<button title="Reverse position">⇄</button><button title="Modify position">✎</button>'
             '<button title="Close position" onclick="document.getElementById(\'cm\').style.display=\'block\'">✕</button>')
    p = tpage(html=_close_page(icons=icons, cell=True))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=False, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is True and got["chosen"] == 2, got


def test_flatten_controls_markup_is_masked_for_the_log(tpage):
    icons = ('<button data-position-id="abcdef12-3456-7890-abcd-ef1234567890" onclick="window.__reverse=1"></button>'
             '<button data-id="9876543210" onclick="window.__modify=1"></button>'
             '<button onclick="window.__closed=1"><span>deadbeefcafe</span></button>')
    p = tpage(html=_close_page(icons=icons, cell=True))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and "0 close-type" in got["why"] and _nothing_pressed(p)
    blob = " ".join(c["html"] for c in got["controls"])
    assert "abcdef12" not in blob and "9876543210" not in blob and "deadbeefcafe" not in blob
    assert "#####" in blob and "########" in blob


def test_watched_close_refuses_an_actions_cell_whose_only_child_is_the_chart_x(tpage):
    # the chart's overlay x (a canvas beside it) rendered inside the row's actions cell
    icons = '<canvas width="40" height="20"></canvas><button title="Close" onclick="window.__chart_x=1">✕</button>'
    p = tpage(html=_close_page(icons=icons, cell=True))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and got["clicked"] is False and "beside a canvas" in got["why"], got
    assert _nothing_pressed(p)


def test_dump_tables_prints_the_position_row_controls_markup_masked(tpage):
    row = POSITION_ROW[:-1] + ["29/09/26 12:56"]
    html = _split_page([row], close_html=_LIVE_TRIO.replace('class="btn-icon"', 'class="btn-icon" data-pid="1234567890"', 1), cell=True)
    lines = DXtradeAdapter(timeout_ms=3_000).dump_tables(tpage(html=html), ())
    ctl = next(ln for ln in lines if ".first_row_control_html:" in ln)
    assert "icon-close" in ctl and "icon-reverse" in ctl and "#####" in ctl and "1234567890" not in ctl
    pair = next(ln for ln in lines if ".pairing:" in ln and "first_row_controls=['button', 'button', 'button']" in ln)
    assert "paired_body=True" in pair
    # one markup line per view tab (the replica shows the same positions table under
    # each), and every one of them is the position row's: the watchlist gets none
    ctl_lines = [ln for ln in lines if ".first_row_control_html:" in ln]
    assert len(ctl_lines) == 4 and all("icon-close" in ln for ln in ctl_lines), ctl_lines


def _close_modal_without_discard(lots="0.005", aria='aria-label="Close position"'):
    """The modal with NO Discard / Cancel and no x: the only button is
    "Close Position", carrying an aria-label that also reads /close/ (the
    shape the independent review of #14013 reproduced: the dismiss fallback
    tagged THAT button as discard and a read-back mismatch pressed it)."""
    return f"""
<div id="cm" style="display:none;position:fixed;left:400px;top:200px;background:#fff;padding:10px">
  <h3>Close SOLUSD Buy Position</h3>
  <label>Lots to Close <input id="lots" value="{lots}"></label>
  <div>0.01 out of 0.01</div>
  <button {aria} onclick="window.__closed=(window.__closed||0)+1;document.getElementById('cm').style.display='none';
                   document.querySelector('tr[data-row-id]:not(.instrument)').remove()">Close Position</button>
</div>"""


@pytest.mark.parametrize("aria", ['aria-label="Close position"', 'aria-label="close"', 'aria-label="Dismiss and close"', ""])
def test_watched_close_mismatch_without_discard_presses_nothing_and_falls_back_to_escape(tpage, aria):
    # REGRESSION (review of #14013): with no Discard / Cancel, the "Close
    # Position" button must never be the dismiss fallback, whatever its
    # aria-label says. A mismatch presses nothing, Escape is the fallback
    # (the replica ignores it, so the modal stays open), and the close is refused.
    p = tpage(html=_close_page(modal=_close_modal_without_discard(aria=aria)))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is False and got["clicked"] is True and "lots-to-close 0.005 != the full size 0.01" in got["why"], got
    assert "no Discard control found" in got["why"] and "Discard pressed" not in got["why"]
    assert got["modal"]["discard"] == 0 and got["modal"]["confirm"] == 1
    assert p.evaluate("window.__closed") is None, "Close Position was pressed on a mismatch"
    assert p.evaluate("document.querySelector('[data-metis-modal-btn=discard]')") is None
    assert p.evaluate("document.querySelectorAll('tr[data-row-id]:not(.instrument)').length") == 1     # the row stays
    assert p.evaluate("document.getElementById('lots').value") == "0.005"                              # never edited


def test_watched_close_matching_modal_without_discard_still_confirms(tpage):
    # the same Discard-less modal reading back correctly is confirmed exactly once
    p = tpage(html=_close_page(modal=_close_modal_without_discard(lots="0.01")))
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01, entry_price=118.94)
    assert got["ok"] is True and got["why"] == "Close Position confirmed" and got["modal"]["discard"] == 0, got
    assert p.evaluate("window.__closed") == 1


def test_discard_modal_refuses_a_discard_tag_that_reads_close_position(tpage):
    # belt and braces on the Python side: even a mis-tagged discard control is
    # not pressed when it reads "Close Position"
    p = tpage(html=_close_page(modal=_close_modal_without_discard(lots="0.005")))
    p.evaluate("document.getElementById('cm').style.display='block';"
               "document.querySelector('#cm button').setAttribute('data-metis-modal-btn', 'discard')")
    assert DXtradeAdapter._discard_modal(p) is False
    assert p.evaluate("window.__closed") is None


def test_watched_close_never_touches_the_chart_overlay_or_a_close_all(tpage):
    # the row's own control is missing; the chart's overlay x and a panel-level x are the only x's
    html = _close_page(icons="").replace('<div data-active="true">Positions</div>',
                                         '<div data-active="true">Positions</div><button title="Close panel">×</button>')
    p = tpage(html=html)
    got = DXtradeAdapter(timeout_ms=3_000).flatten(p, "SOLUSD", arm=True, side="long", quantity=0.01)
    assert got["ok"] is False and got["clicked"] is False and _nothing_pressed(p)


def test_close_modal_and_row_fact_helpers_are_pure():
    from src.prop.platform.dxtrade import _close_modal_mismatch, _row_facts_mismatch
    ok = {"ok": True, "heading": "Close SOLUSD Buy Position", "lots": "0.01", "caption": ["0.01", "0.01"],
          "confirm": 1, "confirm_enabled": True}
    assert _close_modal_mismatch(ok, "SOLUSD", "long", 0.01) == []
    assert _close_modal_mismatch({**ok, "confirm": 2}, "SOLUSD", "long", 0.01) == ['2 "Close Position" buttons (need exactly 1)']
    assert _close_modal_mismatch({**ok, "confirm_enabled": False}, "SOLUSD", "long", 0.01) == ['"Close Position" is disabled']
    assert _close_modal_mismatch({"ok": False, "why": "no modal"}, "SOLUSD", "long", 0.01) == ["no modal"]
    # without the caller's size, the input must still equal the caption's total
    assert _close_modal_mismatch({**ok, "lots": "0.005"}, "SOLUSD", "long", None) == ["lots-to-close 0.005 != the position size 0.01"]
    facts = {"side": "Buy", "size": "0.01", "fill": "118.94"}
    assert _row_facts_mismatch(facts, "long", 0.01, 118.94) == []
    assert _row_facts_mismatch(facts, None, None, None) == []
    assert _row_facts_mismatch({"side": None, "size": None, "fill": None}, "long", 0.01, 118.94) == [
        "side: row shows None, want long", "size: row shows None, want 0.01", "fill: row shows None, want 118.94"]


# ── the wiring: the round trip's close passes the row facts and refuses an
# orphan order; close-position closes and journals an existing position ──


def test_round_trip_close_passes_the_row_facts_and_refuses_an_orphan_order(tmp_path):
    ad, api = RTAdapter(), FakeApi()
    res = rt(ad, api, tmp_path)
    assert res.halted is None, res.actions
    assert ad.flatten_facts == {"side": "long", "quantity": 0.1, "entry_price": 100.1}
    assert any(a["what"] == "after_close" for a in res.actions)
    ad = RTAdapter()
    ad.orphan_after_close = True
    res = rt(ad, FakeApi(), tmp_path)
    assert res.halted == "round trip: close not confirmed"
    assert any("orphan SL/TP" in a for a in res.alerts) and any(a["what"] == "orphan_orders" for a in res.actions)


def _open_pos():
    return Position(symbol="SOLUSD", side="long", quantity=0.01, entry_price=118.94, unrealized_pnl=None,
                    stop_loss=117.8, take_profit=120.18)


def test_close_position_dry_locates_and_live_closes_and_journals(tmp_path):
    c = rt_cfg()
    ledger = pe.IntentLedger(tmp_path / "l.jsonl")
    ledger.record("roundtrip-solusd-20260929T062633Z", "submitted")     # the live test's unresolved ticket
    ad = RTAdapter()
    ad.positions = [_open_pos()]
    res = pe.run_close_position(adapter=ad, page=None, api=FakeApi(), cfg=c, ledger=ledger, venue_symbol="SOLUSD",
                                arm=False, now=NOW)
    assert res.halted is None and [x[0] for x in ad.calls] == ["flatten"]
    assert ad.flatten_facts == {"side": "long", "quantity": 0.01, "entry_price": 118.94}
    assert next(a for a in res.actions if a["what"] == "close_position_spec")["ticket_id"] == "roundtrip-solusd-20260929T062633Z"
    assert ad.positions and ledger.state("roundtrip-solusd-20260929T062633Z") == "submitted"   # nothing changed
    api = FakeApi()
    ad = RTAdapter()
    ad.positions = [_open_pos()]
    res = pe.run_close_position(adapter=ad, page=None, api=api, cfg=c, ledger=ledger, venue_symbol="SOLUSD",
                                arm=True, now=NOW)
    assert res.halted is None, res.actions
    assert [(p["status"], p["direction"], p["qty"], p["entry_price"], p["sl"], p["tp"]) for p in api.posts] == [
        ("open", "long", 0.01, 118.94, 117.8, 120.18), ("closed", "long", 0.01, 118.94, 117.8, 120.18)]
    assert api.posts[0]["ticket_id"] == "roundtrip-solusd-20260929T062633Z"
    assert ledger.state("roundtrip-solusd-20260929T062633Z") == "closed"
    assert ad.positions == [] and any(a["what"] == "close_position_done" for a in res.actions)


def test_close_position_refuses_without_exactly_one_position_and_when_the_close_fails(tmp_path):
    c = rt_cfg()
    ledger = pe.IntentLedger(tmp_path / "l.jsonl")
    res = pe.run_close_position(adapter=RTAdapter(), page=None, api=FakeApi(), cfg=c, ledger=ledger,
                                venue_symbol="SOLUSD", arm=True, now=NOW)
    assert "need exactly 1 SOLUSD position" in res.halted and ledger.latest() == {}
    ad = RTAdapter(close_works=False)
    ad.positions = [_open_pos()]
    api = FakeApi()
    res = pe.run_close_position(adapter=ad, page=None, api=api, cfg=c, ledger=ledger, venue_symbol="SOLUSD",
                                arm=True, now=NOW)
    assert "close NOT confirmed flat" in res.halted
    assert [p["status"] for p in api.posts] == ["open"]                     # the fill is journaled, the close is not
    tid = next(a for a in res.actions if a["what"] == "close_position_spec")["ticket_id"]
    assert tid.startswith("closeout-solusd-") and ledger.state(tid) == "close_unconfirmed"
    # ...and a close that did not confirm is never an unresolved SUBMIT
    assert tid not in ledger.watched() and tid not in ledger.unresolved()


def test_a_close_that_did_not_confirm_never_trips_the_reconcile(env):
    # REGRESSION (go-live 2026-09-29 18:44Z): the day's two test round trips
    # had left their refused / hand-closed CLOSES ledgered "unconfirmed"; the
    # venue closed both positions hours earlier; three live ticks later the
    # reconcile read them as unconfirmed submits, counted three misses and
    # latched AUTO-REVERT on nothing.
    ledger, state = env
    spec = {"ticket_id": "roundtrip-solusd-stale", "venue_symbol": "SOLUSD", "side": "long",
            "quantity": 0.01, "stop_loss": 119.36, "take_profit": 121.78, "order_type": "market"}
    ledger.record("roundtrip-solusd-stale", "intended", spec=spec, purpose="round_trip_test")
    ledger.record("roundtrip-solusd-stale", "close_unconfirmed", purpose="round_trip_close")
    # a LEGACY row, exactly as the pre-fix code left it on the VM (state
    # "unconfirmed" with a close purpose): read as a close, never watched
    ledger.record("roundtrip-solusd-legacy", "intended", spec={**spec, "ticket_id": "roundtrip-solusd-legacy"})
    ledger.record("roundtrip-solusd-legacy", "unconfirmed", purpose="round_trip_close")
    # the negative control: a real unconfirmed SUBMIT is still watched
    ledger.record("t-real-submit", "submitted", spec={**spec, "ticket_id": "t-real-submit"})
    ledger.record("t-real-submit", "unconfirmed", misses=2)
    assert "t-real-submit" in ledger.watched() and "t-real-submit" in ledger.unresolved()
    for stale in ("roundtrip-solusd-stale", "roundtrip-solusd-legacy"):
        assert stale not in ledger.watched() and stale not in ledger.unresolved(), stale
    for _ in range(4):
        res = run(FakeAdapter(), FakeApi([]), env)
        # never re-read as a submit: no confirm / skip action for a close row
        assert not any(str(a.get("ticket_id", "")).startswith("roundtrip-solusd-") and a["what"] != "close_resolved"
                       for a in res.actions), res.actions
    # the stale closes never tripped anything; the real submit did, on its own
    assert state.halted() and "t-real-submit" in state.halted()
    assert not any("roundtrip-solusd-" in a for a in res.alerts)
    # and, the terminal being flat, both closes resolved on the first clean read
    assert ledger.state("roundtrip-solusd-stale") == "close_confirmed"
    assert ledger.state("roundtrip-solusd-legacy") == "close_confirmed"


_STALE_SPEC = {"ticket_id": "roundtrip-solusd-stale", "venue_symbol": "SOLUSD", "side": "long",
               "quantity": 0.01, "stop_loss": 119.36, "take_profit": 121.78, "order_type": "market"}


def _stale_close(ledger, tid="roundtrip-solusd-stale", **extra):
    ledger.record(tid, "intended", spec={**_STALE_SPEC, "ticket_id": tid}, purpose="round_trip_test")
    ledger.record(tid, "close_unconfirmed", purpose="round_trip_close", **extra)


def _placing_env(tmp_path):
    ledger, state = pe.IntentLedger(tmp_path / "l.jsonl"), pe.ExecutorState(tmp_path)
    state.save({"day": pe.trading_day(NOW), "day_start_captured": 4724.0})
    return ledger, state


def _cycle(ad, api, env):
    ledger, state = env
    return pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(breach_guards="report"), mode="live",
                        ledger=ledger, state=state, now=NOW)


def test_a_stale_close_with_the_terminal_flat_resolves_and_releases_entries(tmp_path):
    # (i) review of #14388: resolves to close_confirmed on a clean flat read,
    # never trips, and the SAME tick goes on to place the waiting ticket
    env = _placing_env(tmp_path)
    ledger, state = env
    _stale_close(ledger)
    ad = FakeAdapter(account=acct(4724.0, 4724.0), after_submit=([], [_o(quantity=37.5)]))
    res = _cycle(ad, FakeApi([ticket(qty=37.5)]), env)
    assert ledger.state("roundtrip-solusd-stale") == "close_confirmed"
    assert [a for a in res.actions if a["what"] == "close_resolved"][0]["reason"] == "flat_on_terminal"
    assert not any(a["what"] == "hold" for a in res.actions) and state.halted() is None and res.halted is None
    assert ("place_bracket", "prop-manual-aaa", True) in ad.calls
    assert not ledger.close_unresolved()


def test_a_stale_close_with_the_position_still_found_holds_entries_and_does_not_trip(tmp_path):
    # (ii) the position may be live under its bracket: hold, name it every
    # tick, place nothing, latch nothing
    env = _placing_env(tmp_path)
    ledger, state = env
    _stale_close(ledger)
    ad = FakeAdapter(account=acct(4724.0, 4724.0), positions=[_p(quantity=0.01, entry_price=120.62)])
    for _ in range(2):
        res = _cycle(ad, FakeApi([ticket(qty=37.5)]), env)
        pend = [a for a in res.actions if a["what"] == "close_pending"]
        assert len(pend) == 1 and pend[0]["ticket_id"] == "roundtrip-solusd-stale", res.actions
        hold = [a for a in res.actions if a["what"] == "hold"]
        assert len(hold) == 1 and "roundtrip-solusd-stale" in hold[0]["why"] and "close" in hold[0]["why"]
    assert ledger.state("roundtrip-solusd-stale") == "close_unconfirmed"
    assert not any(c[0] == "place_bracket" for c in ad.calls)
    assert state.halted() is None and res.halted is None and res.alerts == []


def test_a_genuinely_unconfirmed_entry_submit_still_trips(tmp_path):
    # (iii) the negative control, beside a stale close: only the submit trips
    env = _placing_env(tmp_path)
    ledger, state = env
    _stale_close(ledger)
    ledger.record("t-real-submit", "submitted", spec={**_STALE_SPEC, "ticket_id": "t-real-submit"})
    ledger.record("t-real-submit", "unconfirmed", misses=2)
    res = _cycle(FakeAdapter(account=acct(4724.0, 4724.0)), FakeApi([]), env)
    assert state.halted() and "t-real-submit" in state.halted() and "t-real-submit" in res.halted
    assert ledger.state("t-real-submit") == "skipped"
    assert ledger.state("roundtrip-solusd-stale") == "close_confirmed"      # resolved, flat; not the trip


def test_a_failed_terminal_read_never_resolves_a_close(tmp_path):
    # (iv) "could not look" is not "flat": the row stays, entries stay held
    env = _placing_env(tmp_path)
    ledger, state = env
    _stale_close(ledger)
    ad = FakeAdapter(account=acct(4724.0, 4724.0), read_error="selector drift")
    res = _cycle(ad, FakeApi([ticket(qty=37.5)]), env)
    assert res.halted and "terminal read failed" in res.halted
    assert ledger.state("roundtrip-solusd-stale") == "close_unconfirmed" and ledger.close_unresolved()
    assert not any(c[0] == "place_bracket" for c in ad.calls)
    assert not any(a["what"] in ("close_resolved", "close_pending") for a in res.actions)


def test_a_legacy_closeout_row_without_a_spec_resolves_from_its_id(tmp_path):
    env = _placing_env(tmp_path)
    ledger, state = env
    ledger.record("closeout-solusd-20260929T125601Z", "open", purpose="close_position", entry=120.62)
    ledger.record("closeout-solusd-20260929T125601Z", "unconfirmed", purpose="close_position")
    ad = FakeAdapter(account=acct(4724.0, 4724.0), positions=[_p(quantity=0.01)])
    res = _cycle(ad, FakeApi([]), env)
    assert [a["ticket_id"] for a in res.actions if a["what"] == "close_pending"] == ["closeout-solusd-20260929T125601Z"]
    ad.positions = []
    _cycle(ad, FakeApi([]), env)
    assert ledger.state("closeout-solusd-20260929T125601Z") == "close_confirmed" and state.halted() is None


def test_round_trip_refused_close_is_ledgered_as_a_close_not_a_submit(tmp_path):
    ad, api = RTAdapter(close_works=False), FakeApi()
    res = rt(ad, api, tmp_path, reads=2)
    assert res.halted == "round trip: close not confirmed"
    ledger = pe.IntentLedger(tmp_path / "l.jsonl")                          # rt()'s ledger path
    tid = next(a["spec"]["ticket_id"] for a in res.actions if a["what"] == "round_trip_spec")
    assert ledger.state(tid) == "close_unconfirmed" and tid not in ledger.watched()


def test_tick_close_position_modes_and_apply_tokens():
    from scripts.prop.prop_executor_tick import resolve_mode
    ns = lambda **k: SimpleNamespace(**{"probe_ticket": "", "dry_run": False, "watched_click": False,  # noqa: E731
                                        "round_trip": "", "close_position": "", "live": False, **k})
    assert resolve_mode(ns(close_position="SOLUSD"), {}) == "close_position_dry"
    assert resolve_mode(ns(close_position="SOLUSD", live=True), {}) == "not_armed"
    assert resolve_mode(ns(close_position="SOLUSD", live=True), {pe.MODE_ENV: "live"}) == "close_position_live"
    sh = (Path(__file__).resolve().parents[1] / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    assert 'close-position)      EARGS+=(--close-position "${RT_SYMBOL}") ;;' in sh
    assert 'close-position-live) EARGS+=(--close-position "${RT_SYMBOL}" --live) ;;' in sh
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "system-actions.yml").read_text()
    assert "|close-position|close-position-live)" in wf


def test_a_non_enabled_symbol_is_decided_once_and_never_reported_on_later_cycles(env):
    ledger, state = env
    eth = ticket(ticket_id="prop-eth", symbol="ETHUSDT")
    api = FakeApi([eth])
    for _ in range(3):
        pe.run_cycle(adapter=FakeAdapter(), page=None, api=api, cfg=_sol_only(), mode="live",
                     ledger=ledger, state=state, now=NOW)
    assert not any(p.get("ticket_id") == "prop-eth" for p in api.posts)
    assert [r for r in ledger._rows() if r["ticket_id"] == "prop-eth"] == \
        [r for r in ledger._rows() if r["ticket_id"] == "prop-eth"][:1]


# ── a resting entry is withdrawn at its ticket's valid_until (BREAKOUT-ATTRITION) ──


def _placed_then(env, now, valid_until, orders, positions=()):
    ledger, state = env
    ad = FakeAdapter(after_submit=([], [_o()]))
    api = FakeApi([ticket(valid_until=valid_until)])
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state, now=NOW)
    assert ledger.state("prop-manual-aaa") == "placed"
    ad.orders, ad.positions, ad.calls = list(orders), list(positions), []
    api._tickets = []
    res = pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state, now=now)
    return ad, api, res


def test_a_resting_entry_past_valid_until_is_cancelled_and_confirmed_by_reread(env):
    vu = (NOW + timedelta(minutes=30)).isoformat()
    later = NOW + timedelta(minutes=31)
    ad, api, res = _placed_then(env, later, vu, [_o()])
    assert ad.calls == [("cancel_order", "O1", True)]
    assert env[0].latest()["prop-manual-aaa"]["cancel_requested"]
    # gone on two reads -> reported skipped with the expiry as its reason
    ad.orders = []
    ad.calls = []
    for k in (10, 15):
        pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=env[0], state=env[1],
                     now=later + timedelta(minutes=k))
    skips = [p for p in api.posts if p.get("status") == "skipped" and p.get("ticket_id") == "prop-manual-aaa"]
    assert len(skips) == 1 and skips[0]["reason"].startswith("expired")
    assert env[1].halted() is None


def test_a_resting_entry_within_valid_until_is_left_alone(env):
    vu = (NOW + timedelta(minutes=30)).isoformat()
    ad, _, _ = _placed_then(env, NOW + timedelta(minutes=10), vu, [_o()])
    assert ad.calls == []


def test_a_filled_entry_is_never_cancelled_at_valid_until(env):
    vu = (NOW + timedelta(minutes=30)).isoformat()
    ad, _, _ = _placed_then(env, NOW + timedelta(hours=2), vu, [], positions=[_p()])
    assert not any(c[0] == "cancel_order" for c in ad.calls)
    assert env[0].state("prop-manual-aaa") == "open"


def test_a_ledger_row_without_valid_until_is_never_guessed_at(env):
    ledger, state = env
    ledger.record("legacy", "placed", spec=dict(SPEC, ticket_id="legacy"))
    ad = FakeAdapter(orders=[_o()])
    pe.run_cycle(adapter=ad, page=None, api=FakeApi([]), cfg=cfg(), mode="live", ledger=ledger,
                 state=state, now=NOW + timedelta(days=3))
    assert not any(c[0] == "cancel_order" for c in ad.calls)


def test_read_only_never_cancels_a_resting_entry(env):
    ledger, state = env
    ledger.record("t1", "placed", spec=SPEC, valid_until=(NOW - timedelta(hours=1)).isoformat())
    ad = FakeAdapter(orders=[_o()])
    pe.run_cycle(adapter=ad, page=None, api=FakeApi([]), cfg=cfg(), mode="read_only", ledger=ledger,
                 state=state, now=NOW)
    assert all(arm is False for (k, _, arm) in ad.calls if k == "cancel_order")


# ── review of #14581 (manager, 2026-09-30 06:55Z) ─────────────────────────


def test_an_expired_non_enabled_ticket_is_not_reported_expired_either(env):
    # A ticket first seen after its valid_until (tick outage > TTL, or the
    # first live cycle after read_only) must stay with the manual bridge.
    ledger, state = env
    eth = ticket(ticket_id="prop-eth", symbol="ETHUSDT", valid_until=(NOW - timedelta(minutes=5)).isoformat())
    api = FakeApi([eth])
    pe.run_cycle(adapter=FakeAdapter(), page=None, api=api, cfg=_sol_only(), mode="live",
                 ledger=ledger, state=state, now=NOW)
    assert not any(p.get("ticket_id") == "prop-eth" for p in api.posts)
    assert ledger.latest()["prop-eth"]["reason"] == "symbol_not_enabled"


class _FailingCancel(FakeAdapter):
    def cancel_order(self, page, order, *, arm=False):
        self.calls.append(("cancel_order", order.order_id, arm))
        return {"ok": False, "clicked": False, "why": "need exactly 1 row"}


def test_a_failed_cancel_is_not_recorded_as_requested_and_retries_then_gives_up_loudly(env):
    ledger, state = env
    vu = (NOW + timedelta(minutes=30)).isoformat()
    ad = _FailingCancel(after_submit=([], [_o()]))
    api = FakeApi([ticket(valid_until=vu)])
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state, now=NOW)
    api._tickets = []
    alerts = []
    for k in range(pe.CANCEL_EXPIRED_MAX_ATTEMPTS + 2):
        ad.calls = []
        res = pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger,
                           state=state, now=NOW + timedelta(minutes=31 + 5 * k))
        alerts.append(res.alerts)
        row = ledger.latest()["prop-manual-aaa"]
        assert not row.get("cancel_requested")
        if k < pe.CANCEL_EXPIRED_MAX_ATTEMPTS:
            assert [c[0] for c in ad.calls] == ["cancel_order"]
        else:
            assert ad.calls == []  # exhausted: no more clicks
    assert all(any("retrying" in a for a in al) for al in alerts[:pe.CANCEL_EXPIRED_MAX_ATTEMPTS - 1])
    assert any("STILL RESTING" in a for a in alerts[pe.CANCEL_EXPIRED_MAX_ATTEMPTS - 1])
    assert not any(a for a in alerts[pe.CANCEL_EXPIRED_MAX_ATTEMPTS:])  # one alert at exhaustion
    assert not any(p.get("status") == "skipped" for p in api.posts)


def test_a_partial_fill_showing_the_full_order_size_is_contained_and_halts(env):
    # 0.2 of 0.5 filled while the order row still reads its full 0.5: the
    # order matches, the 0.2 position does not.
    vu = (NOW + timedelta(minutes=30)).isoformat()
    ad, api, res = _placed_then(env, NOW + timedelta(minutes=31), vu, [_o()],
                                positions=[_p(quantity=0.2)])
    assert not any(c[0] == "cancel_order" for c in ad.calls)
    assert env[0].state("prop-manual-aaa") == "contained"
    assert env[1].halted() and "partial_fill_suspected" in env[1].halted()
    assert not any(p.get("status") == "skipped" for p in api.posts)


def test_an_earlier_transient_miss_does_not_shorten_the_post_cancel_skip(env):
    ledger, state = env
    vu = (NOW + timedelta(minutes=30)).isoformat()
    ad = FakeAdapter(after_submit=([], [_o()]))
    api = FakeApi([ticket(valid_until=vu)])
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state, now=NOW)
    api._tickets = []
    ad.orders = []  # one transient miss before expiry
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                 now=NOW + timedelta(minutes=5))
    assert ledger.latest()["prop-manual-aaa"]["misses"] == 1
    ad.orders = [_o()]
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                 now=NOW + timedelta(minutes=31))
    assert ledger.latest()["prop-manual-aaa"]["cancel_requested"]
    ad.orders = []
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                 now=NOW + timedelta(minutes=36))
    assert not any(p.get("status") == "skipped" for p in api.posts)  # 1 read, not yet 2
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                 now=NOW + timedelta(minutes=41))
    assert [p["reason"] for p in api.posts if p.get("status") == "skipped"][0].startswith("expired")


def test_a_new_ticket_in_the_cancel_cycle_is_held_not_refused(env):
    ledger, state = env
    vu = (NOW + timedelta(minutes=30)).isoformat()
    ad = FakeAdapter(after_submit=([], [_o()]))
    api = FakeApi([ticket(valid_until=vu)])
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state, now=NOW)
    later = NOW + timedelta(minutes=31)
    api._tickets = [ticket(ticket_id="prop-new", created_at=later.isoformat(),
                           valid_until=(later + timedelta(hours=1)).isoformat())]
    res = pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                       now=later)
    assert "prop-new" not in ledger.latest()  # not refused, not recorded: decided on the next read
    assert any(a["what"] == "hold" and a.get("ticket_id") == "prop-new" for a in res.actions)
    assert not any(p.get("ticket_id") == "prop-new" for p in api.posts)


# ── round-2 review of #14581 (manager, 2026-09-30 07:15Z) ─────────────────


def test_a_clicked_cancel_whose_order_is_still_there_is_a_failed_attempt_and_retries(env):
    # _row_action returns ok/clicked with "outcome unknown" when the click
    # raised: the next read, not the click, decides.
    ledger, state = env
    vu = (NOW + timedelta(minutes=30)).isoformat()
    later = NOW + timedelta(minutes=31)
    ad, api, _ = _placed_then(env, later, vu, [_o()])  # attempt 1 clicked, order stays
    alerts = []
    for k in range(1, pe.CANCEL_EXPIRED_MAX_ATTEMPTS + 2):
        ad.calls = []
        res = pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger,
                           state=state, now=later + timedelta(minutes=5 * k))
        alerts.append((len([c for c in ad.calls if c[0] == "cancel_order"]), res.alerts))
    # attempts 2..MAX are retried with an alert each; the MAX-th read exhausts
    assert [n for n, _ in alerts[:pe.CANCEL_EXPIRED_MAX_ATTEMPTS - 1]] == [1] * (pe.CANCEL_EXPIRED_MAX_ATTEMPTS - 1)
    assert all(any("did not take" in a for a in al) for _, al in alerts[:pe.CANCEL_EXPIRED_MAX_ATTEMPTS - 1])
    n_ex, al_ex = alerts[pe.CANCEL_EXPIRED_MAX_ATTEMPTS - 1]
    assert n_ex == 0 and any("STILL RESTING" in a for a in al_ex)
    assert ledger.latest()["prop-manual-aaa"]["cancel_exhausted"] is True
    assert alerts[-1] == (0, [])  # 5 min later: no click, and no re-alert yet
    assert not any(p.get("status") == "skipped" for p in api.posts)


def test_an_exhausted_cancel_re_alerts_hourly_while_the_order_rests(env):
    ledger, state = env
    ledger.record("t1", "placed", spec=SPEC, valid_until=(NOW - timedelta(hours=2)).isoformat(),
                  cancel_attempts=3, cancel_exhausted=True, exhausted_alert_at=(NOW - timedelta(minutes=30)).isoformat())
    ad, api = FakeAdapter(orders=[_o()]), FakeApi([])
    r1 = pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state, now=NOW)
    assert not any("STILL RESTING" in a for a in r1.alerts)
    r2 = pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                      now=NOW + timedelta(minutes=31))
    assert any("STILL RESTING" in a for a in r2.alerts)
    assert not any(c[0] == "cancel_order" for c in ad.calls)


def test_reviewer_scenario_partial_fill_with_no_stop_fails_closed_end_to_end(env):
    # Round-3 review: a 0.3 remainder order + a 0.2 position with NO SL/TP,
    # cycles every 5 min to T+300 (valid_until T+30), the position closes at
    # T+305. It must never be silent, never cancel on a guess, and never be
    # written off as "gone without a fill".
    ledger, state = env
    vu = (NOW + timedelta(minutes=30)).isoformat()
    ad, api, res = _placed_then(env, NOW + timedelta(minutes=5), vu, [_o(quantity=0.3)],
                                positions=[_p(quantity=0.2, stop_loss=None, take_profit=None)])
    first = res.alerts
    assert any("partial_fill_suspected" in a and "halted" in a for a in first)
    assert any("NO STOP OR TARGET" in a for a in first)
    assert ledger.state("prop-manual-aaa") == "contained"
    assert state.halted()
    later_alerts = []
    for k in range(10, 305, 5):
        r = pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                         now=NOW + timedelta(minutes=k))
        later_alerts += r.alerts
    # the claim is released: the unjournaled position is flagged as an orphan
    assert any("orphan" in a for a in later_alerts)
    ad.positions = []  # the position closes; the remainder still rests
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                 now=NOW + timedelta(minutes=305))
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                 now=NOW + timedelta(minutes=310))
    assert not any(c[0] == "cancel_order" for c in ad.calls)  # nothing cancelled on a guess
    assert not any(p.get("status") == "skipped" for p in api.posts)  # never written off
    assert state.halted()  # still halted until a person clears it


def test_a_manual_position_on_the_symbol_side_does_not_hide_behind_a_resting_claim(env):
    # (d): an unrelated position must not be absorbed by the resting row's
    # claim; the row is contained and the orphan check sees the position.
    ledger, state = env
    vu = (NOW + timedelta(minutes=30)).isoformat()
    ad, api, res = _placed_then(env, NOW + timedelta(minutes=5), vu, [_o()], positions=[_p(quantity=3.0)])
    assert ledger.state("prop-manual-aaa") == "contained" and state.halted()
    r = pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                     now=NOW + timedelta(minutes=10))
    assert any("orphan" in a for a in r.alerts)


def test_a_partial_fill_before_the_first_confirm_read_is_never_written_off_unconfirmed(env):
    # Round-4 review: 0.2 of 0.5 fills before the first re-read; the orders
    # table shows the remaining 0.3, so the verdict is not_found on an
    # `unconfirmed` row. It must alert and halt at once and never post
    # `skipped: unconfirmed_submit`.
    ledger, state = env
    ad = FakeAdapter(after_submit=([_p(quantity=0.2)], [_o(quantity=0.3)]))
    api = FakeApi([ticket()])
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state, now=NOW)
    assert ledger.state("prop-manual-aaa") == "unconfirmed"
    api._tickets = []
    res = pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                       now=NOW + timedelta(minutes=5))
    assert any("partial_fill_suspected" in a for a in res.alerts)
    assert state.halted() and ledger.state("prop-manual-aaa") == "contained"
    for k in (10, 15, 20):
        pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger, state=state,
                     now=NOW + timedelta(minutes=k))
    assert not any(p.get("status") == "skipped" for p in api.posts)
    assert not any("unconfirmed_submit" in str(p.get("reason")) for p in api.posts)



# ── the live form opens in MARKET mode: the price field only exists after
#    LIMIT is selected (2026-09-30 12:44Z, ticket prop-manual-b573aecb5d47,
#    prop_fills #73 "not submitted: form fields not found: ['price']") ──────

MARKET_DEFAULT_PAGE = (TICKET_PAGE
    .replace('onclick="window.__type=\'market\';sel(this)">Market</button>',
             'aria-pressed="true" onclick="window.__type=\'market\';sel(this);'
             'document.getElementById(\'pxrow\').style.display=\'none\'">Market</button>')
    .replace('onclick="window.__type=\'limit\';sel(this)">Limit</button>',
             'onclick="window.__type=\'limit\';sel(this);'
             'document.getElementById(\'pxrow\').style.display=\'block\'">Limit</button>')
    .replace('<div><span>Price</span><input id="px"></div>',
             '<div id="pxrow" style="display:none"><span>Price</span><input id="px"></div>'))


def test_the_market_default_fixture_really_hides_price_until_limit(tpage):
    assert MARKET_DEFAULT_PAGE.count('id="pxrow" style="display:none"') == 1
    got = DXtradeAdapter(timeout_ms=3_000).probe_order_ticket(tpage(html=MARKET_DEFAULT_PAGE), "SOLUSD")
    assert "price" not in got["fields"] and {"quantity", "stop_loss", "take_profit"} <= set(got["fields"])


def test_a_limit_bracket_selects_limit_before_requiring_the_price_field(tpage):
    p = tpage(html=MARKET_DEFAULT_PAGE)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=False)
    assert att.stage == "form_verified", att.detail
    assert p.evaluate("window.__type") == "limit"
    assert p.evaluate("window.__submits") is None


def test_a_limit_bracket_refuses_when_limit_shows_no_price_field(tpage):
    html = MARKET_DEFAULT_PAGE.replace("document.getElementById('pxrow').style.display='block'", "0")
    p = tpage(html=html)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, SOL, arm=True)
    assert att.stage == "refused" and not att.submitted
    assert "price" in att.detail and "LIMIT" in att.detail
    assert p.evaluate("window.__submits") is None


def test_a_market_bracket_never_needs_a_price_field(tpage):
    from dataclasses import replace
    p = tpage(html=MARKET_DEFAULT_PAGE)
    att = DXtradeAdapter(timeout_ms=3_000).place_bracket(p, replace(SOL, order_type="market", limit_price=None), arm=False)
    assert att.stage == "form_verified", att.detail



def test_a_ticket_the_form_refuses_raises_a_not_placed_alert(env):
    ad = FakeAdapter(attempt=PlaceAttempt(stage="refused", submitted=False,
                                          detail="form fields not found: ['price']"))
    api = FakeApi([ticket()])
    res = run(ad, api, env)
    assert any("NOT PLACED yet" in a and "prop-manual-aaa" in a and "price" in a and "1/3" in a
               and "do NOT place by hand" in a for a in res.alerts)
    assert not any("place by hand or it is lost" in a for a in res.alerts)
    assert not any(p.get("ticket_id") == "prop-manual-aaa" for p in api.posts)



def test_a_limit_round_trip_is_dry_only(env):
    ledger, _ = env
    c = cfg(watched_click_max_lots={"SOLUSD": 0.01})
    res = pe.run_round_trip(adapter=FakeAdapter(), page=None, api=FakeApi([]), cfg=c, ledger=ledger,
                            venue_symbol="SOLUSD", lots=0.01, arm=True, order_type="limit", now=NOW)
    assert res.halted and "dry-only" in res.halted


def test_a_dry_limit_round_trip_walks_a_limit_spec_at_the_resting_quote(env):
    ledger, _ = env
    c = cfg(watched_click_max_lots={"SOLUSD": 0.01})

    class Q(FakeAdapter):
        def read_quote(self, page, venue):
            return {"bid": 120.0, "ask": 120.1}

    ad = Q()
    res = pe.run_round_trip(adapter=ad, page=None, api=FakeApi([]), cfg=c, ledger=ledger,
                            venue_symbol="SOLUSD", lots=0.01, arm=False, order_type="limit", now=NOW)
    spec = [a for a in res.actions if a["what"] == "round_trip_spec"][0]["spec"]
    assert spec["order_type"] == "limit" and spec["limit_price"] == 120.0
    assert ad.calls == [("place_bracket", spec["ticket_id"], False), ("flatten", "SOLUSD", False)]



# ── retry exception (operator directive 2026-09-30 ~13:12Z): a ticket whose
#    placement failed BEFORE any submit click is retried until valid_until ──

class QuoteAdapter(FakeAdapter):
    """A retry reads the quote exactly as a first attempt does (the entry-band
    check, PI-20260930-KMYJ5XC7-0003); it counts the reads."""
    def read_quote(self, page, venue):
        self.quote_reads = getattr(self, "quote_reads", 0) + 1
        return super().read_quote(page, venue)


PRE = PlaceAttempt(stage="refused", submitted=False, detail="form fields not found: ['price']")


def _rcycle(ad, api, env, k):
    ledger, state = env
    return pe.run_cycle(adapter=ad, page=None, api=api, cfg=cfg(), mode="live", ledger=ledger,
                        state=state, now=NOW + timedelta(minutes=5 * k))


def _places(ad):
    return [c for c in ad.calls if c[0] == "place_bracket"]


def test_retry_places_the_same_ticket_again_once_the_form_works(env):
    # (5) the idempotency key is unchanged: same ticket id, one order
    ad = QuoteAdapter(attempt=PRE)
    api = FakeApi([ticket()])
    _rcycle(ad, api, env, 0)
    assert env[0].state("prop-manual-aaa") == pe.RETRY_STATE
    ad.attempt, ad.after_submit = None, ([], [_o()])
    _rcycle(ad, api, env, 1)
    assert [c[1] for c in _places(ad)] == ["prop-manual-aaa", "prop-manual-aaa"]
    assert env[0].state("prop-manual-aaa") == "placed"
    assert [p.get("status") for p in api.posts if p.get("ticket_id") == "prop-manual-aaa"] == ["placed"]


def test_a_failure_after_the_submit_click_is_never_retried(env):
    # (1) planted negative: the submit click raised -> submitted=True ->
    # the unconfirmed/containment path, never retry_pending, never a 2nd click
    ad = QuoteAdapter(
                      attempt=PlaceAttempt(stage="submitted", submitted=True,
                                           detail="submit click raised TimeoutError; outcome unknown"),
                      after_submit=([], []))
    api = FakeApi([ticket()])
    for k in range(4):
        _rcycle(ad, api, env, k)
    assert env[0].state("prop-manual-aaa") != pe.RETRY_STATE
    assert len(_places(ad)) == 1


def test_a_retry_is_refused_when_the_symbol_already_has_a_position_or_order(env):
    # (2) the terminal is re-read THIS cycle; anything on the symbol -> no click
    ad = QuoteAdapter(attempt=PRE)
    api = FakeApi([ticket()])
    _rcycle(ad, api, env, 0)
    ad.attempt = None
    ad.orders = [_o(order_id="MANUAL")]
    _rcycle(ad, api, env, 1)
    assert len(_places(ad)) == 1  # only the original attempt
    assert env[0].state("prop-manual-aaa") == "refused"


def test_retries_are_bounded_then_terminal_skipped_with_one_alert(env):
    # (4) RETRY_MAX_ATTEMPTS attempts, then terminal skipped with the real reason
    ad = QuoteAdapter(attempt=PRE)
    api = FakeApi([ticket()])
    alerts = []
    for k in range(pe.RETRY_MAX_ATTEMPTS + 2):
        alerts.append(_rcycle(ad, api, env, k).alerts)
    assert len(_places(ad)) == pe.RETRY_MAX_ATTEMPTS
    assert env[0].state("prop-manual-aaa") == "refused"
    skips = [p for p in api.posts if p.get("ticket_id") == "prop-manual-aaa"]
    assert len(skips) == 1 and "price" in skips[0]["reason"] and f"{pe.RETRY_MAX_ATTEMPTS} attempts" in skips[0]["reason"]
    assert sum("place by hand or it is lost" in a for al in alerts for a in al) == 1
    assert sum("do NOT place by hand" in a for al in alerts for a in al) == pe.RETRY_MAX_ATTEMPTS - 1


def test_a_retry_pending_ticket_goes_terminal_at_valid_until(env):
    # (6) at valid_until the ticket is expired as today, never attempted again
    ad = QuoteAdapter(attempt=PRE)
    api = FakeApi([ticket(valid_until=(NOW + timedelta(minutes=7)).isoformat())])
    _rcycle(ad, api, env, 0)
    ad.attempt = None
    _rcycle(ad, api, env, 2)  # NOW+10 > valid_until
    assert len(_places(ad)) == 1
    assert env[0].state("prop-manual-aaa") == "expired"
    assert [p["reason"] for p in api.posts if p.get("ticket_id") == "prop-manual-aaa"] == ["expired"]


def test_a_retry_runs_the_same_guards_as_a_first_attempt(env):
    # manager 2026-09-30 13:21Z: no retry-only price rule. A retry whose
    # ticket no longer fits the § 3.3 guards is refused like a first attempt.
    ad = QuoteAdapter(attempt=PRE)
    api = FakeApi([ticket()])
    _rcycle(ad, api, env, 0)
    ad.attempt = None
    ad.account = acct(4724.0, 4724.0)
    env[1].save({**env[1].load(), "day": pe.trading_day(NOW), "day_start_captured": 4724.0})
    api._tickets = [ticket(qty=37.5)]
    _rcycle(ad, api, env, 1)
    assert len(_places(ad)) == 1
    assert env[0].state("prop-manual-aaa") == "refused"



def test_a_watched_click_ticket_is_never_retried(env):
    # review of #14737: an unattended retry would place it at FULL size, not
    # the watched cap -> terminal at once, with the give-up alert
    ledger, state = env
    c = cfg()
    ad = QuoteAdapter(attempt=PRE)
    api = FakeApi([ticket()])
    res = pe.run_cycle(adapter=ad, page=None, api=api, cfg=c, mode="live", ledger=ledger, state=state,
                       now=NOW, max_lots=0.5)
    assert ledger.state("prop-manual-aaa") == "refused"
    assert any("place by hand or it is lost" in a and "watched click" in a for a in res.alerts)
    skips = [p for p in api.posts if p.get("ticket_id") == "prop-manual-aaa"]
    assert len(skips) == 1 and "watched click: not retried" in skips[0]["reason"]
    ad.attempt = None
    pe.run_cycle(adapter=ad, page=None, api=api, cfg=c, mode="live", ledger=ledger, state=state,
                 now=NOW + timedelta(minutes=5))
    assert len(_places(ad)) == 1


# ── entry-band check on BOTH attempt kinds (PI-20260930-KMYJ5XC7-0003,
#    manager 2026-09-30 13:29Z / 21:03Z): this cycle's quote must lie inside
#    the ticket's own entry band before any placement ──

B573_TEXT = ("e.\n  Entry    : 121.34   (only if live price is within 120.691339 … 121.988661)\n"
             "  Stop     : 118.74535714")


def test_the_band_parser_reads_the_real_ticket_text():
    assert pe._entry_band({"message": B573_TEXT}) == (120.691339, 121.988661)
    assert pe._entry_band({"message": "within 1.5 ... 2.5"}) == (1.5, 2.5)
    assert pe._entry_band({"message": "no band here"}) is None
    assert pe._entry_band({"message": "within 3 … 2"}) is None          # inverted: unreadable
    assert pe._entry_band({}) is None


def _band_cycle(ad, api, env, k=0, c=None):
    ledger, state = env
    return pe.run_cycle(adapter=ad, page=None, api=api, cfg=c or cfg(), mode="live", ledger=ledger,
                        state=state, now=NOW + timedelta(minutes=5 * k))


def test_a_first_attempt_waits_while_the_price_is_beyond_the_stop(env):
    # the danger: a BUY limit at 120 with the ask at 117.5 (below the 118 SL)
    # is marketable and fills straight into its own stop
    ledger, _ = env
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})
    api = FakeApi([ticket()])
    res = _band_cycle(ad, api, env)
    assert _places(ad) == [] and ledger.latest() == {}                 # WAIT: not an attempt
    assert any(a["what"] == "band_wait" and "117.5" in a["why"] for a in res.actions)
    assert not [p for p in api.posts if p.get("kind") == "fill"]
    ad.quote = {"bid": 119.99, "ask": 120.0}                           # back inside the band
    _band_cycle(ad, api, env, 1)
    assert len(_places(ad)) == 1


def test_a_first_attempt_waits_above_the_band_too(env):
    ad = FakeAdapter(quote={"bid": 120.9, "ask": 121.0})               # band 119.5..120.5
    _band_cycle(ad, FakeApi([ticket()]), env)
    assert _places(ad) == []


def test_a_short_is_checked_on_the_bid(env):
    t = ticket(direction="short", sl=122.0, tp=114.0)                  # band 119.5..120.5
    ad = FakeAdapter(quote={"bid": 120.8, "ask": 120.0})               # ask inside, bid outside
    _band_cycle(ad, FakeApi([t]), env)
    assert _places(ad) == []
    ad.quote = {"bid": 120.0, "ask": 120.8}
    _band_cycle(ad, FakeApi([t]), env, 1)
    assert len(_places(ad)) == 1


def test_an_unreadable_quote_waits(env):
    ledger, _ = env

    class Blind(FakeAdapter):
        def read_quote(self, page, venue):
            raise LookupError("watchlist row not found")

    ad = Blind()
    res = _band_cycle(ad, FakeApi([ticket()]), env)
    assert _places(ad) == [] and ledger.latest() == {}
    assert any(a["what"] == "band_wait" and "could not look" in a["why"] for a in res.actions)
    ad2 = FakeAdapter(quote={"bid": None, "ask": None})
    _band_cycle(ad2, FakeApi([ticket()]), env, 1)
    assert _places(ad2) == [] and ledger.latest() == {}


def test_an_unreadable_band_is_terminal_with_one_alert(env):
    ledger, _ = env
    ad = FakeAdapter()
    api = FakeApi([ticket(message="no band in this text")])
    res = _band_cycle(ad, api, env)
    assert _places(ad) == []
    assert ledger.state("prop-manual-aaa") == "refused"
    assert [a for a in res.alerts if "entry band unreadable" in a and "place it by hand" in a]
    assert [p for p in api.posts if p.get("status") == "skipped"]
    res = _band_cycle(ad, api, env, 1)                                  # final: not re-decided
    assert _places(ad) == [] and not [a for a in res.alerts if "entry band" in a]


def test_a_waiting_ticket_still_expires_at_valid_until(env):
    ledger, _ = env
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})
    api = FakeApi([ticket()])
    _band_cycle(ad, api, env, 0)
    _band_cycle(ad, api, env, 7)                                        # NOW+35m > valid_until (NOW+30m)
    assert _places(ad) == [] and ledger.state("prop-manual-aaa") == "expired"


def test_a_retry_waits_outside_the_band_without_spending_an_attempt(env):
    ledger, _ = env
    ad = QuoteAdapter(attempt=PRE)
    api = FakeApi([ticket()])
    _rcycle(ad, api, env, 0)                                            # attempt 1: pre-submit failure
    assert ledger.state("prop-manual-aaa") == pe.RETRY_STATE
    ad.attempt, ad.quote = None, {"bid": 117.4, "ask": 117.5}
    _rcycle(ad, api, env, 1)                                            # beyond the stop: WAIT
    assert len(_places(ad)) == 1 and ledger.state("prop-manual-aaa") == pe.RETRY_STATE
    assert ledger.latest()["prop-manual-aaa"].get("attempts") == 1
    ad.quote = {"bid": 119.99, "ask": 120.0}
    _rcycle(ad, api, env, 2)
    assert len(_places(ad)) == 2 and ledger.state("prop-manual-aaa") != pe.RETRY_STATE


def test_the_band_check_runs_for_a_second_account_too(env):
    # per account: the check is in run_cycle, which every account runs
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})
    _band_cycle(ad, FakeApi([ticket()]), env, c=cfg(account_id="tradeify_1"))
    assert _places(ad) == []


def test_a_blind_wait_alerts_once_per_ticket(env):
    # manager review of #14908: a persistently unreadable quote must not let
    # a live ticket expire with only a log line
    ad = FakeAdapter(quote={"bid": None, "ask": None})
    api = FakeApi([ticket()])
    res = _band_cycle(ad, api, env, 0)
    blind = [a for a in res.alerts if "live price unreadable, NOT placed yet" in a]
    assert len(blind) == 1 and "prop-manual-aaa" in blind[0] and "place by hand if needed" in blind[0]
    res = _band_cycle(ad, api, env, 1)
    assert not [a for a in res.alerts if "live price unreadable" in a]      # once per ticket
    api._tickets = [ticket(), ticket(ticket_id="prop-manual-bbb")]
    res = _band_cycle(ad, api, env, 2)                                   # a waiting ticket is no candidate,
    blind = [a for a in res.alerts if "live price unreadable" in a]      # so the next one is checked too
    assert len(blind) == 1 and "prop-manual-bbb" in blind[0]            # its own first alert; aaa's not repeated


def test_an_outside_band_wait_does_not_alert(env):
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})
    res = _band_cycle(ad, FakeApi([ticket()]), env)
    assert not [a for a in res.alerts if "unreadable" in a]


@pytest.mark.parametrize("ask", [119.5, 120.5])                     # ticket()'s band is 119.5..120.5
def test_a_price_exactly_on_a_band_edge_places(env, ask):
    ad = FakeAdapter(quote={"bid": ask - 0.01, "ask": ask})
    _band_cycle(ad, FakeApi([ticket()]), env)
    assert len(_places(ad)) == 1


def test_a_dry_unreadable_band_alerts_once(env):
    ledger, state = env
    api = FakeApi([ticket(message="no band in this text")])
    alerts = []
    for k in range(3):
        res = pe.run_cycle(adapter=FakeAdapter(), page=None, api=api, cfg=cfg(), mode="read_only",
                           ledger=ledger, state=state, now=NOW + timedelta(minutes=5 * k))
        alerts += [a for a in res.alerts if "entry band unreadable" in a]
    assert len(alerts) == 1


def test_a_passing_band_check_is_logged_with_the_quote_it_used(env):
    # manager 2026-09-30 22:06Z: a live pass must be observable, not inferred
    ad = FakeAdapter(quote={"bid": 119.99, "ask": 120.0})
    res = _band_cycle(ad, FakeApi([ticket()]), env)
    ok = [a for a in res.actions if a["what"] == "band_ok"]
    assert len(ok) == 1 and ok[0]["why"] == "ask 120.0 inside the ticket's entry band 119.5..120.5"
    assert len(_places(ad)) == 1


# ── the dry round trip restores the linked symbol it moved (manager review of #15002) ──


class _LinkingAdapter(FakeAdapter):
    """Models the terminal's linked symbol: the dry place_bracket switches it to the ticket's symbol."""

    def __init__(self, linked="SOLUSD", fail_restore=False, raise_in_place=False, **kw):
        super().__init__(**kw)
        self.linked, self.fail_restore, self.raise_in_place = linked, fail_restore, raise_in_place

    def read_linked_symbol(self, page):
        self.calls.append(("read_linked_symbol",))
        return self.linked

    def select_linked_symbol(self, page, sym):
        self.calls.append(("select_linked_symbol", sym))
        before = self.linked
        if self.fail_restore:
            return {"ok": False, "clicked": True, "before": before, "after": before, "why": "did not follow"}
        self.linked = sym
        return {"ok": True, "clicked": before != sym, "before": before, "after": sym}

    def place_bracket(self, page, spec, *, arm=False):
        self.linked = spec.venue_symbol
        if self.raise_in_place:
            self.calls.append(("place_bracket", spec.ticket_id, arm))
            raise RuntimeError("terminal went away")
        return super().place_bracket(page, spec, arm=arm)


def test_dry_round_trip_restores_and_verifies_the_original_linked_symbol(env):
    ledger, _ = env
    ad = _LinkingAdapter(linked="SOLUSD")
    res = pe.run_round_trip(adapter=ad, page=None, api=FakeApi(), cfg=_eth_not_enabled(), ledger=ledger,
                            venue_symbol="ETHUSD", arm=False)
    names = [c[0] for c in ad.calls]
    assert names.index("read_linked_symbol") < names.index("place_bracket") < names.index("select_linked_symbol")
    assert ("select_linked_symbol", "SOLUSD") in ad.calls and ad.linked == "SOLUSD"
    assert not any("RESTORE" in a for a in res.alerts)


def test_dry_round_trip_restores_even_when_the_walk_raises(env):
    ledger, _ = env
    ad = _LinkingAdapter(linked="SOLUSD", raise_in_place=True)
    with pytest.raises(RuntimeError):
        pe.run_round_trip(adapter=ad, page=None, api=FakeApi(), cfg=_eth_not_enabled(), ledger=ledger,
                          venue_symbol="ETHUSD", arm=False)
    assert ("select_linked_symbol", "SOLUSD") in ad.calls and ad.linked == "SOLUSD"


def test_a_failed_restore_is_an_alert(env):
    ledger, _ = env
    ad = _LinkingAdapter(linked="SOLUSD", fail_restore=True)
    res = pe.run_round_trip(adapter=ad, page=None, api=FakeApi(), cfg=_eth_not_enabled(), ledger=ledger,
                            venue_symbol="ETHUSD", arm=False)
    assert any("RESTORE FAILED" in a and "SOLUSD" in a for a in res.alerts)


def test_an_unreadable_link_before_the_walk_is_an_alert_and_nothing_is_reselected(env):
    ledger, _ = env
    ad = _LinkingAdapter(linked=None)
    res = pe.run_round_trip(adapter=ad, page=None, api=FakeApi(), cfg=_eth_not_enabled(), ledger=ledger,
                            venue_symbol="ETHUSD", arm=False)
    assert not any(c[0] == "select_linked_symbol" for c in ad.calls)
    assert any("could not be read" in a for a in res.alerts)


def test_review_a_failed_or_unreadable_restore_fails_the_round_trip_exit(env):
    # Manager review of #15020: a failed restore exited 0 because the tick's
    # round-trip branch only checked res.halted. Both link alerts must fail it.
    ledger, _ = env
    for ad in (_LinkingAdapter(linked="SOLUSD", fail_restore=True), _LinkingAdapter(linked=None)):
        res = pe.run_round_trip(adapter=ad, page=None, api=FakeApi(), cfg=_eth_not_enabled(), ledger=ledger,
                                venue_symbol="ETHUSD", arm=False)
        assert not res.halted and pe.round_trip_failed(res) is True
    ok = pe.run_round_trip(adapter=_LinkingAdapter(linked="SOLUSD"), page=None, api=FakeApi(),
                           cfg=_eth_not_enabled(), ledger=ledger, venue_symbol="ETHUSD", arm=False)
    assert not any(str(a).startswith(pe.LINK_RESTORE_ALERTS) for a in ok.alerts)
    assert pe.round_trip_failed(ok) is False


def test_review_the_tick_exits_3_when_a_dry_restore_fails(env, capsys):
    # CLI-level (manager re-review of #15020): the tick's round-trip branch
    # returns emit_round_trip(res); a real dry round trip whose restore fails
    # (or whose link is unreadable) must exit 3, and a clean one 0.
    from scripts.prop import prop_executor_tick as tick
    ledger, _ = env
    for ad, want in ((_LinkingAdapter(linked="SOLUSD", fail_restore=True), tick.EXIT_UNPARSED),
                     (_LinkingAdapter(linked=None), tick.EXIT_UNPARSED),
                     (_LinkingAdapter(linked="SOLUSD"), tick.EXIT_OK)):
        res = pe.run_round_trip(adapter=ad, page=None, api=FakeApi(), cfg=_eth_not_enabled(), ledger=ledger,
                                venue_symbol="ETHUSD", arm=False)
        assert not res.halted and tick.emit_round_trip(res) == want
    out = capsys.readouterr().out
    assert "RESTORE FAILED" in out and '"executor": "done"' in out
    src = (Path(__file__).resolve().parents[1] / "scripts/prop/prop_executor_tick.py").read_text()
    branch = src[src.index('if mode.startswith("round_trip") or mode.startswith("close_position"):'):
                 src.index("res = pe.run_cycle(")]
    assert "code = emit_round_trip(res, *secrets)" in branch and "return code" in branch
    assert "if res.halted else" not in branch


@pytest.mark.parametrize("fail_restore,linked,want", [(True, "SOLUSD", 3), (False, None, 3), (False, "SOLUSD", 0)])
def test_review_cli_round_trip_dry_exit_code_follows_the_restore(tmp_path, monkeypatch, capsys,
                                                                 fail_restore, linked, want):
    # REAL CLI (manager re-review of #15020): the whole tick.main(["--round-trip", ...]) path --
    # argument parsing, mode resolution, the session branch, run_round_trip and the exit code -- with
    # only the outside world faked: the browser (a no-op page), the platform config, the adapter, the
    # executor config and the local API.
    import contextlib
    import types
    pytest.importorskip("playwright.sync_api")
    import playwright.sync_api as pw_api
    from scripts.prop import prop_executor_tick as tick

    page = types.SimpleNamespace(wait_for_timeout=lambda ms: None)
    context = types.SimpleNamespace(new_page=lambda: page)
    browser = types.SimpleNamespace(new_context=lambda **kw: context, close=lambda: None)
    monkeypatch.setattr(pw_api, "sync_playwright", lambda: contextlib.nullcontext(
        types.SimpleNamespace(chromium=types.SimpleNamespace(launch=lambda **kw: browser))))

    class _CliAdapter(_LinkingAdapter):
        timeout_ms = 3_000

        def login(self, page, url, username, password):
            self.calls.append(("login",))

        def wait_ready(self, page, timeout_ms=None):
            return True

    ad = _CliAdapter(linked=linked, fail_restore=fail_restore)
    monkeypatch.setenv("METIS_TEST_USER", "zq9-fake-user")
    monkeypatch.setenv("METIS_TEST_PASS", "zq9-fake-pass")
    monkeypatch.setattr(tick, "load_platform_config", lambda account: {
        "platform": "fake", "login_url": "about:blank", "username_env": "METIS_TEST_USER",
        "password_env": "METIS_TEST_PASS"})
    monkeypatch.setattr(tick, "adapter_for_platform", lambda platform: ad)
    monkeypatch.setattr(pe, "load_config", lambda account: _eth_not_enabled())
    monkeypatch.setattr(pe, "LocalApi", lambda *a, **k: FakeApi())
    code = tick.main(["--round-trip", "ETHUSD", "--login", "fresh", "--state-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert ("login",) in ad.calls and any(c[0] == "place_bracket" for c in ad.calls), out
    assert code == want, out
    assert ('RESTORE FAILED' in out or 'could not be read' in out) == (want == 3)


def test_an_armed_round_trip_does_not_read_or_restore_the_link(env):
    ledger, _ = env
    ad = _LinkingAdapter(linked="SOLUSD")
    pe.run_round_trip(adapter=ad, page=None, api=FakeApi(), cfg=_sol_only(), ledger=ledger,
                      venue_symbol="SOLUSD", arm=True, reads=1)
    assert not any(c[0] in ("read_linked_symbol", "select_linked_symbol") for c in ad.calls)


# ── the live tick wins over a secondary test (operator directive ~12:35Z
# 2026-10-01, manager comment 5931584062 on #14947) ─────────────────────────


def test_pending_live_tickets_uses_the_cycles_own_intake_filters(tmp_path):
    led = pe.IntentLedger(tmp_path / "ledger.jsonl")
    led.record("prop-done", "submitted")
    led.record("prop-retry", pe.RETRY_STATE, attempts=1)
    c = cfg(enabled_venue_symbols=["SOLUSD"])
    api = FakeApi(tickets=[
        ticket(ticket_id="prop-fresh"),
        ticket(ticket_id="prop-done"),                                        # final in the ledger
        ticket(ticket_id="prop-retry"),                                       # retry_pending: still waiting
        ticket(ticket_id="prop-stale", valid_until=(NOW - timedelta(minutes=1)).isoformat()),
        ticket(ticket_id="prop-eth", symbol="ETHUSDT"),                       # no enabled venue
    ])
    assert pe.pending_live_tickets(api, c, led, now=NOW) == ["prop-fresh", "prop-retry"]


def test_a_secondary_mode_defers_before_any_browser_while_a_live_ticket_waits(tmp_path, monkeypatch, capsys):
    import scripts.prop.prop_executor_tick as tick
    monkeypatch.setattr(pe, "pending_live_tickets", lambda api, cfg, ledger, now=None: ["prop-x"])
    monkeypatch.setattr(tick, "adapter_for_platform", lambda name: (_ for _ in ()).throw(AssertionError("no browser")))
    storage = tmp_path / "s.json"
    storage.write_text("{}")
    code = tick.main(["--symbol-switch-dry", "ETHUSD", "--state-dir", str(tmp_path),
                      "--storage-state", str(storage)])
    assert code == tick.EXIT_DEFERRED and "the executor tick wins" in capsys.readouterr().out
    assert "close_position_dry" not in tick.YIELD_MODES and "live" not in tick.YIELD_MODES


def test_the_action_wrapper_reports_a_deferred_test_as_deferred_not_failed():
    sh = (Path(__file__).resolve().parents[1] / "scripts/ops/breakout_login_check_action.sh").read_text()
    i = sh.index('if [ "${rc}" -eq 7 ]; then')
    block = sh[i:i + 400]
    assert "deferred" in block and "exit 0" in block


def test_contain_passes_the_accounts_rollout_latch_and_a_guard_refusal_falls_through_to_close(env):
    # DIALOG-MEASURE rollout guard (manager 2026-10-02): the partial_no_sl_tp
    # repair types SL AND TP, so under the guard it is refused before any
    # click; the existing next-cycle close-at-market then takes over.
    ledger, _ = env
    ledger.record("t1", "submitted", spec=SPEC)
    ad = FakeAdapter(positions=[_p(stop_loss=None)])
    ad.modify_result = {"ok": False, "clicked": False,
                        "why": "rollout refused: rollout step must leave the take profit unchanged"}
    res = run(ad, FakeApi(), env)
    assert ("modify_bracket", "SOLUSD", True) in ad.calls
    assert ad.rollout.path == Path(ledger.path).parent / "modify_rollout.json"
    assert any("rollout refused" in al for al in res.alerts)
    run(ad, FakeApi(), env)
    assert ("flatten", "SOLUSD", True) in ad.calls


# ── BREAKOUT-NOATTEMPT: a validity that ran out unattempted is never silent ──
#
# MEASURED 2026-10-02 from the ict-prop-executor journal (06:59Z-16:59Z, 121
# ticks, read via /api/diag/journalctl): ETH ticket prop-manual-fb466451fd1f
# (created 08:19:49Z, valid_until 09:19:50Z) was looked at on all 12 ticks
# inside its validity and declined every one with band_wait (ask 2747.43 ..
# 2760.52 against an entry band around 2774.55). It reached expiry_prompted
# with NO prop_fills row and NO alert, which reads exactly like an executor
# that never attempted it. These tests pin the alert that distinguishes them.


def test_a_ticket_that_waits_out_its_whole_validity_alerts_once(env):
    ledger, state = env
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})        # outside ticket()'s 119.5..120.5 band
    api = FakeApi([ticket()])
    # inside the validity (valid_until = NOW + 30m): waits, and stays quiet
    for k in (0, 1, 2):
        res = _band_cycle(ad, api, env, k)
        assert _places(ad) == [] and ledger.latest() == {}
        assert [a for a in res.alerts if "NOT PLACED" in a] == []
    # the expiry prompter has since flipped it off `emitted`, so intake is empty
    api._tickets = []
    res = _band_cycle(ad, api, env, 7)                           # NOW + 35m > valid_until
    hit = [a for a in res.alerts if "NOT PLACED" in a]
    assert len(hit) == 1 and "prop-manual-aaa" in hit[0]
    assert "NO placement attempt" in hit[0] and "entry band" in hit[0]
    assert any(a["what"] == "unattempted_expiry" and a["ticket_id"] == "prop-manual-aaa"
               for a in res.actions)
    # ...and only once, however many cycles follow
    for k in (8, 9):
        assert [a for a in _band_cycle(ad, api, env, k).alerts if "NOT PLACED" in a] == []


def test_the_no_attempt_alert_posts_no_report_and_writes_no_ledger_row(env):
    # A `skipped` report would flip the ticket off `emitted` and pull it out of
    # every manual-bridge path that keys on it. The alert must not do that.
    ledger, _ = env
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})
    api = FakeApi([ticket()])
    _band_cycle(ad, api, env, 0)
    api._tickets = []
    res = _band_cycle(ad, api, env, 7)
    assert [a for a in res.alerts if "NOT PLACED" in a]
    # the routine account_status report still goes; nothing about the TICKET does
    assert [p for p in api.posts if p.get("kind") != "account_status"] == []
    assert ledger.latest() == {}


def test_a_ticket_that_comes_back_into_the_band_and_places_never_alerts(env):
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})
    api = FakeApi([ticket()])
    _band_cycle(ad, api, env, 0)                                # waits
    ad.quote = {"bid": 119.99, "ask": 120.0}                    # back inside
    _band_cycle(ad, api, env, 1)
    assert len(_places(ad)) == 1
    res = _band_cycle(ad, api, env, 7)                          # past valid_until
    assert [a for a in res.alerts if "NOT PLACED" in a] == []


def test_a_ticket_refused_on_its_band_does_not_also_get_the_no_attempt_alert(env):
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})
    api = FakeApi([ticket()])
    _band_cycle(ad, api, env, 0)                                # waits: note taken
    api._tickets = [ticket(message="no band in this text")]     # unreadable band → refuse
    _band_cycle(ad, api, env, 1)
    res = _band_cycle(ad, api, env, 7)
    assert [a for a in res.alerts if "NO placement attempt" in a] == []


def test_a_symbol_not_enabled_ticket_alerts_when_its_validity_runs_out(env):
    # MEASURED 2026-09-30: ETH ticket prop-manual-a22cb1d44517 was skipped
    # `symbol_not_enabled` at 12:34:59Z (ETHUSD joined enabled_venue_symbols
    # only on 2026-10-01, #15143) and aged to expiry_prompted with no row.
    ledger, state = env
    c = cfg(enabled_venue_symbols=["SOLUSD"],
            symbols={"SOLUSDT": {"venue": "SOLUSD", "cvpp": 1.0, "lot_units": 1.0,
                                 "min_lots": 0.01, "lot_step": 0.01},
                     "ETHUSDT": {"venue": "ETHUSD", "cvpp": 1.0, "lot_units": 1.0,
                                 "min_lots": 0.01, "lot_step": 0.01}})
    t = ticket(ticket_id="prop-manual-eth", symbol="ETHUSDT", entry=2774.55,
               sl=2729.59, tp=3044.30)
    api = FakeApi([t])
    ad = FakeAdapter()
    res = pe.run_cycle(adapter=ad, page=None, api=api, cfg=c, mode="live", ledger=ledger,
                       state=state, now=NOW)
    assert any(a["what"] == "skipped" and a.get("reason") == "symbol_not_enabled"
               for a in res.actions)
    assert [a for a in res.alerts if "NOT PLACED" in a] == []    # still inside its validity
    api._tickets = []
    res = pe.run_cycle(adapter=ad, page=None, api=api, cfg=c, mode="live", ledger=ledger,
                       state=state, now=NOW + timedelta(minutes=35))
    hit = [a for a in res.alerts if "NOT PLACED" in a]
    assert len(hit) == 1 and "prop-manual-eth" in hit[0]
    assert "enabled venue symbols" in hit[0]


def test_the_sweep_fires_even_when_the_ticket_read_fails(env):
    # The sweep runs BEFORE intake, so an API outage cannot hide the expiry.
    ledger, state = env
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})
    _band_cycle(ad, FakeApi([ticket()]), env, 0)

    class Dead(FakeApi):
        def tickets(self, account_id):
            raise OSError("api down")

    res = _band_cycle(ad, Dead(), env, 7)
    assert [a for a in res.alerts if "NO placement attempt" in a]
    assert [a for a in res.alerts if "ticket intake failed" in a]


def test_a_ticket_whose_validity_has_not_passed_is_not_alerted(env):
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})
    api = FakeApi([ticket()])
    for k in range(6):                                          # NOW .. NOW+25m, valid_until NOW+30m
        assert [a for a in _band_cycle(ad, api, env, k).alerts if "NOT PLACED" in a] == []


def test_a_ticket_with_no_readable_valid_until_is_never_swept(env):
    # Fail-quiet: a validity we cannot read is not known to have passed, and a
    # false "NOT PLACED" on a live setup is worse than silence.
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})
    api = FakeApi([ticket(valid_until=None)])
    for k in (0, 20):
        assert [a for a in _band_cycle(ad, api, env, k).alerts if "NOT PLACED" in a] == []


def test_the_no_attempt_note_store_is_bounded(env):
    ledger, state = env
    ad = FakeAdapter(quote={"bid": 117.4, "ask": 117.5})
    for i in range(pe._UNATTEMPTED_KEEP + 12):
        _band_cycle(ad, FakeApi([ticket(ticket_id=f"prop-manual-{i:03d}")]), env, 0)
    assert len(state.load().get(pe._UNATTEMPTED_KEY) or {}) <= pe._UNATTEMPTED_KEEP


def test_attempt_public_carries_the_masked_sidebar_text():
    """TRADEIFY-SOL-SIZE: a submit-stage refusal records the ticket column's
    visible text (digits masked) so the run log can show a validation line."""
    sb = {"found": True, "n": 1, "rows": [{"tag": "div", "text": "Minimum quantity is ## lots", "hint": True}]}
    att = PlaceAttempt(stage="refused", detail="submit: disabled", form={"sidebar_text": sb})
    assert pe._attempt_public(att)["sidebar_text"] == sb
    assert pe._attempt_public(PlaceAttempt(stage="refused", detail="x", form={}))["sidebar_text"] is None


def test_sidebar_text_js_masks_digits_and_skips_personal():
    from src.prop.platform.dxtrade import SIDEBAR_TEXT_JS
    assert "replace(/\\d/g, '#')" in SIDEBAR_TEXT_JS
    assert "user|profile|account|login|email" in SIDEBAR_TEXT_JS
    assert ".value" not in SIDEBAR_TEXT_JS        # input values are never read


# ── TRADEIFY-SOL-SIZE: the DRY-only limit offset probe ───────────────────────

def _lim_cfg():
    c = rt_cfg()
    c.symbols["SOLUSDT"]["price_step"] = 0.001
    return c


@pytest.mark.parametrize("pct,expect", [(-0.5, 99.6), (0.5, 100.6)])
def test_dry_limit_offset_prices_from_the_ask(tmp_path, pct, expect):
    ad = RTAdapter(quote={"bid": 100.0, "ask": 100.1})
    res = rt(ad, FakeApi(), tmp_path, arm=False, c=_lim_cfg(), order_type="limit", limit_offset_pct=pct)
    assert res.halted is None, res.actions
    spec = [a for a in res.actions if a["what"] == "round_trip_spec"][0]
    # within one 0.001 tick of the exact ask * (1 + pct), on the requested side of the ask
    assert spec["spec"]["limit_price"] == pytest.approx(100.1 * (1 + pct / 100), abs=0.001)
    assert spec["spec"]["limit_price"] == pytest.approx(expect, abs=0.01)
    assert (spec["spec"]["limit_price"] < 100.1) == (pct < 0)
    assert spec["limit_offset_pct"] == pct and spec["quote_at_spec"] == {"bid": 100.0, "ask": 100.1}
    after = [a for a in res.actions if a["what"] == "quote_after_submit_check"]
    assert after and after[0]["quote"] == {"bid": 100.0, "ask": 100.1}
    assert all(c[2] is False for c in ad.calls)          # nothing armed


@pytest.mark.parametrize("kw,needle", [
    (dict(arm=True, order_type="limit"), "dry-only"),     # LIMIT refuses arming first
    (dict(arm=True, order_type="market"), "dry-only"),
    (dict(arm=False, order_type="market"), "needs order_type 'limit'"),
    (dict(arm=False, order_type="limit", limit_offset_pct=7.0), "outside +/-5%"),
])
def test_limit_offset_is_refused_before_any_click(tmp_path, kw, needle):
    kw = {"limit_offset_pct": 0.5, **kw}
    ad = RTAdapter()
    res = rt(ad, FakeApi(), tmp_path, c=_lim_cfg(), **kw)
    assert needle in (res.halted or "") and ad.calls == []


def test_live_ticket_path_cannot_carry_a_limit_offset():
    """The live executor path (run_cycle -> bracket_from_ticket) has no offset
    parameter: a ticket is always placed at its own entry."""
    import inspect
    assert "limit_offset_pct" not in inspect.signature(pe.run_cycle).parameters
    assert "limit_offset_pct" not in inspect.signature(pe.bracket_from_ticket).parameters
    src = inspect.getsource(pe.run_cycle)
    assert "limit_offset" not in src


def test_market_round_trip_does_not_read_the_quote_twice(tmp_path):
    res = rt(RTAdapter(), FakeApi(), tmp_path, arm=False)
    assert not [a for a in res.actions if a["what"] == "quote_after_submit_check"]


# ── retry in band: a LIMIT blocked at/through the market (operator 2026-10-04) ──
# MEASURED tradeify_1 SOLUSD (#16307/#16310/#16313): the submit is DISABLED and
# the footer reads "Entry Price you set must be lower than ...".

def _blocked(text="Entry Price you set must be lower than ###.###"):
    return PlaceAttempt(stage="refused", submitted=False,
                        detail="submit: not visible at its centre after scrolling — ...; the submit is DISABLED, "
                               "the submit has pointer-events:none (not hit-testable)",
                        form={"sidebar_text": {"found": True, "rows": [{"text": "Buy"}, {"text": text, "hint": True}]}})


class SpecAdapter(QuoteAdapter):
    """Records every spec handed to place_bracket (order type, limit)."""
    def place_bracket(self, page, spec, *, arm=False):
        self.specs = getattr(self, "specs", []) + [spec]
        return super().place_bracket(page, spec, arm=arm)


def _await_alerts(alerts):
    return [a for al in alerts for a in al if "sits at/through the market" in a]


def _end_alerts(alerts):
    return [a for al in alerts for a in al if pe.AWAIT_REST_EXPIRED in a]


def test_marketable_limit_block_needs_disabled_and_the_footer_text():
    assert pe.marketable_limit_block(_blocked()) and pe.marketable_limit_block(
        _blocked("Entry Price you set must be higher than ###.###"))
    other = PlaceAttempt(stage="refused", detail="submit: disabled", form={"sidebar_text": {"rows": [{"text": "x"}]}})
    assert pe.marketable_limit_block(other) is None                         # disabled, other cause
    not_disabled = PlaceAttempt(stage="refused", detail="form read-back mismatch",
                                form=_blocked().form)
    assert pe.marketable_limit_block(not_disabled) is None                  # text alone is not enough
    assert pe.marketable_limit_block(PlaceAttempt(stage="submitted", submitted=True, detail="disabled",
                                                  form=_blocked().form)) is None


def test_blocked_marketable_limit_waits_then_is_placed_when_resting(env):
    ledger, _ = env
    ad = SpecAdapter(attempt=_blocked())                 # default quote ask 120.0 == limit 120.0
    api = FakeApi([ticket()])
    alerts = [_rcycle(ad, api, env, 0).alerts]
    assert ledger.state("prop-manual-aaa") == pe.AWAIT_REST_STATE
    assert len(_await_alerts(alerts)) == 1 and not [p for p in api.posts if p.get("ticket_id")]
    ad.attempt = None
    res = _rcycle(ad, api, env, 1)                       # ask still == limit: no form, no click
    alerts.append(res.alerts)
    assert len(_places(ad)) == 1 and any(a["what"] == "awaiting_resting_price" for a in res.actions)
    ad.quote, ad.after_submit = {"bid": 120.1, "ask": 120.2}, ([], [_o()])
    alerts.append(_rcycle(ad, api, env, 2).alerts)       # ask above the limit, inside the band
    assert len(_places(ad)) == 2 and ledger.state("prop-manual-aaa") == "placed"
    assert {s.order_type for s in ad.specs} == {"limit"} and {s.limit_price for s in ad.specs} == {120.0}
    assert len(_await_alerts(alerts)) == 1 and not _end_alerts(alerts)


def test_blocked_marketable_limit_expires_not_placed_with_one_alert(env):
    ledger, _ = env
    ad = SpecAdapter(attempt=_blocked())
    api = FakeApi([ticket(valid_until=(NOW + timedelta(minutes=12)).isoformat())])
    alerts = [_rcycle(ad, api, env, k).alerts for k in (0, 1, 2)]      # blocked, then still marketable
    alerts += [_rcycle(ad, api, env, k).alerts for k in (3, 4)]         # NOW+15/+20 > valid_until
    assert len(_places(ad)) == 1
    row = ledger.latest()["prop-manual-aaa"]
    assert row["state"] == "refused" and pe.AWAIT_REST_EXPIRED in row["reasons"][0]
    assert len(_end_alerts(alerts)) == 1 and len(_await_alerts(alerts)) == 1
    skips = [p for p in api.posts if p.get("ticket_id") == "prop-manual-aaa"]
    assert len(skips) == 1 and pe.AWAIT_REST_EXPIRED in skips[0]["reason"]


def test_an_awaiting_ticket_that_left_intake_still_ends_once_at_valid_until(env):
    ledger, _ = env
    ad = SpecAdapter(attempt=_blocked())
    api = FakeApi([ticket(valid_until=(NOW + timedelta(minutes=7)).isoformat())])
    _rcycle(ad, api, env, 0)
    api._tickets = []                                    # expiry prompt flipped it off `emitted`
    alerts = [_rcycle(ad, api, env, k).alerts for k in (2, 3)]
    assert ledger.state("prop-manual-aaa") == "refused" and len(_end_alerts(alerts)) == 1
    assert not [p for p in api.posts if p.get("ticket_id") == "prop-manual-aaa"]   # never flipped once out of intake


def test_an_awaiting_ticket_ends_when_price_leaves_the_band(env):
    ledger, _ = env
    ad = SpecAdapter(attempt=_blocked())
    api = FakeApi([ticket()])
    _rcycle(ad, api, env, 0)
    ad.attempt, ad.quote = None, {"bid": 120.9, "ask": 121.0}         # band 119.5..120.5
    alerts = [_rcycle(ad, api, env, k).alerts for k in (1, 2)]
    assert len(_places(ad)) == 1 and ledger.state("prop-manual-aaa") == "refused"
    assert len(_end_alerts(alerts)) == 1 and "left the entry band" in _end_alerts(alerts)[0]


def test_an_unrelated_disabled_submit_keeps_the_refuse_and_retry_path(env):
    ledger, _ = env
    other = PlaceAttempt(stage="refused", detail="submit: disabled",
                         form={"sidebar_text": {"rows": [{"text": "Insufficient margin"}]}})
    ad = SpecAdapter(attempt=other)
    api = FakeApi([ticket()])
    res = _rcycle(ad, api, env, 0)
    assert ledger.state("prop-manual-aaa") == pe.RETRY_STATE
    assert ledger.latest()["prop-manual-aaa"]["attempts"] == 1
    assert any("NOT PLACED yet" in a and "1/3" in a for a in res.alerts) and not _await_alerts([res.alerts])


def test_awaiting_does_not_spend_the_retry_budget(env):
    ledger, _ = env
    ad = SpecAdapter(attempt=PRE)                        # a real pre-submit failure: attempt 1
    api = FakeApi([ticket()])
    _rcycle(ad, api, env, 0)
    assert ledger.latest()["prop-manual-aaa"]["attempts"] == 1
    ad.attempt = _blocked()
    _rcycle(ad, api, env, 1)                             # blocked: awaiting, budget untouched
    assert ledger.state("prop-manual-aaa") == pe.AWAIT_REST_STATE
    assert ledger.latest()["prop-manual-aaa"]["attempts"] == 1
    ad.quote = {"bid": 120.1, "ask": 120.2}
    _rcycle(ad, api, env, 2)                             # resting -> attempted again, blocked again (race)
    assert ledger.state("prop-manual-aaa") == pe.AWAIT_REST_STATE
    assert ledger.latest()["prop-manual-aaa"]["attempts"] == 1
    ad.attempt = PRE
    _rcycle(ad, api, env, 3)                             # the next REAL failure is attempt 2, not 4
    assert ledger.state("prop-manual-aaa") == pe.RETRY_STATE
    assert ledger.latest()["prop-manual-aaa"]["attempts"] == 2


def test_a_watched_click_that_is_blocked_is_refused_not_awaited(env):
    ledger, state = env
    ad = SpecAdapter(attempt=_blocked())
    pe.run_cycle(adapter=ad, page=None, api=FakeApi([ticket()]), cfg=cfg(watched_click_max_lots={"SOLUSD": 0.1}),
                 mode="live", ledger=ledger, state=state, now=NOW,
                 max_lots={"SOLUSD": 0.1})
    assert ledger.state("prop-manual-aaa") == "refused"


def test_no_live_ticket_path_sends_market():
    import inspect
    src = inspect.getsource(pe.run_cycle) + inspect.getsource(pe.bracket_from_ticket)
    assert 'order_type="market"' not in src and "order_type='market'" not in src
    spec, _, why = pe.bracket_from_ticket(ticket(), cfg())
    assert not why and spec.order_type == "limit" and spec.limit_price == 120.0


def test_pending_live_tickets_counts_an_awaiting_ticket(env):
    ledger, _ = env
    ledger.record("prop-manual-aaa", pe.AWAIT_REST_STATE, attempts=0)
    assert pe.pending_live_tickets(FakeApi([ticket()]), cfg(), ledger, now=NOW) == ["prop-manual-aaa"]


def test_redactor_keeps_the_quote_after_submit_check_action_name():
    from src.prop.platform.dxtrade import redact_text
    line = '{"action": {"what": "quote_after_submit_check", "quote": {"ask": 121.5}}}'
    assert redact_text(line) == line
    # anything else 24+ chars long is still masked, including a near-miss
    assert "<token>" in redact_text("quote_after_submit_checkX") and "<token>" in redact_text("a" * 30)


# ── OA-05/06 (PI-20261004-GCFA5DOR-0003): a leg lost AFTER `open` is alerted
# and contained; a repair that did not click closes in the SAME cycle; a close
# that did not click stays retryable up to a bound, never parked silently. ──

OPEN_FILL = [{"id": 1, "account_id": "breakout_1", "symbol": "SOLUSDT", "direction": "long",
              "status": "open", "ticket_id": "t1", "sl": 118.0, "tp": 126.0}]
REFUSED = {"ok": False, "clicked": False, "why": "rollout refused: current stop loss not readable on the row"}


class _NoClose(FakeAdapter):
    def flatten(self, page, symbol=None, *, arm=False, **facts):
        self.calls.append(("flatten", symbol, arm))
        return {"ok": False, "clicked": False, "why": "row not located"}


def test_open_row_whose_sl_disappears_alerts_then_is_contained(env):
    ledger, state = env
    ledger.record("t1", "open", spec=SPEC)
    ad = FakeAdapter(positions=[_p(stop_loss=None)])
    ad.modify_result = REFUSED
    res = run(ad, FakeApi([], OPEN_FILL), env)
    assert any("NAKED" in a and "NO SL" in a and "read 1/2" in a for a in res.alerts)
    assert ad.calls == [] and state.halted() is None              # one read never clicks
    res = run(ad, FakeApi([], OPEN_FILL), env)
    assert [c[0] for c in ad.calls] == ["modify_bracket", "flatten"]   # repair refused -> same-cycle close
    assert state.halted() and "t1: naked_open" in state.halted()
    assert any(a.startswith("AUTO-REVERT") for a in res.alerts)
    assert ledger.state("t1") == "contained" and ledger.latest()["t1"]["verdict"] == "naked_open"


def test_open_row_missing_the_tp_the_journal_expects_is_naked(env):
    ledger, state = env
    ledger.record("t1", "open", spec=SPEC)
    ad = FakeAdapter(positions=[_p(take_profit=None)])
    run(ad, FakeApi([], OPEN_FILL), env)
    res = run(ad, FakeApi([], OPEN_FILL), env)
    assert any("NO TP" in a for a in res.alerts) and state.halted()


def test_open_row_with_no_sl_column_on_the_read_is_not_contained(env):
    # No Stop Loss column = we could not look, not "no stop".
    env[0].record("t1", "open", spec=SPEC)
    ad = FakeAdapter(positions=[_p(stop_loss=None, take_profit=None,
                                   raw={"Symbol": "SOLUSD", "Side": "Buy", "Size": "0.5"})])
    for _ in range(3):
        res = run(ad, FakeApi([], OPEN_FILL), env)
    assert ad.calls == [] and env[1].halted() is None
    assert not any("NAKED" in a for a in res.alerts)
    assert any(a["what"] == "naked_check_unreadable" for a in res.actions)


def test_naked_open_position_without_a_ledger_row_halts_and_is_not_touched(env):
    ad = FakeAdapter(positions=[_p(stop_loss=None)])
    run(ad, FakeApi([], OPEN_FILL), env)
    res = run(ad, FakeApi([], OPEN_FILL), env)
    assert ad.calls == [] and env[1].halted() and "naked_open" in env[1].halted()
    assert any("not touched" in a for a in res.alerts)


def test_a_repair_that_did_not_click_closes_in_the_same_cycle(env):
    ledger, _ = env
    ledger.record("t1", "submitted", spec=SPEC)
    ad = FakeAdapter(positions=[_p(stop_loss=None)])
    ad.modify_result = REFUSED
    run(ad, FakeApi(), env)
    assert [c[0] for c in ad.calls] == ["modify_bracket", "flatten"]
    assert ledger.state("t1") == "contained"


def test_a_close_that_did_not_click_is_retried_up_to_the_bound(env):
    ledger, _ = env
    ledger.record("t1", "submitted", spec=SPEC)
    ad = _NoClose(positions=[_p(stop_loss=None)])
    ad.modify_result = REFUSED
    for k in range(1, pe.NAKED_CLOSE_MAX_ATTEMPTS):
        res = run(ad, FakeApi(), env)
        assert ledger.state("t1") == "unconfirmed"                # still watched, not parked
        assert ledger.latest()["t1"]["close_attempts"] == k
        assert any(f"attempt {k}/{pe.NAKED_CLOSE_MAX_ATTEMPTS}" in a for a in res.alerts)
    res = run(ad, FakeApi(), env)
    assert ledger.state("t1") == "contained" and ledger.latest()["t1"]["close_gave_up"] is True
    assert any("gave up" in a for a in res.alerts)
    assert [c[0] for c in ad.calls].count("flatten") == pe.NAKED_CLOSE_MAX_ATTEMPTS
    assert [c[0] for c in ad.calls].count("modify_bracket") == 1
    n = len(ad.calls)
    run(ad, FakeApi(), env)
    assert len(ad.calls) == n                                       # parked: no further clicks
