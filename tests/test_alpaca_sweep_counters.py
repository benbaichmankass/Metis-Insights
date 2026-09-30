"""PI-20260929-K1XNYYAQ-0003 (+ a concrete pin for PR6YRTQY-0005's re-arm cap).

(c) MEASURED 2026-09-29 18:55:56Z on the live trader (running a211c5aa5): the
    equity sweep logged checked=9, covered=7 and every other counter 0. Two
    rows sat in no counter: one was the active-close skip (alpaca_paper/GLD,
    18:55:51Z), and the other went down one of the sweep's uncounted early
    `continue` paths. A skipped row and a lost row read the same. Now every
    checked row lands in exactly one per-row outcome counter, and `unaccounted`
    (checked minus their sum) is 0.
(b) `escalated_post_rejected` counted every falsy re-arm result, including
    those that never reached the venue. The counter is now split.
(a) The top-up `exit_deferred` comment said "no cooldown was left". It was wrong:
    the refused POST set the cooldown. The behaviour is pinned here and the
    comment is corrected.

The fixture and venue are the 2026-09-24 QQQ replay from
tests/test_alpaca_rearm_preflight.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.runtime import order_monitor as om
from tests.test_alpaca_rearm_preflight import (  # noqa: F401 — `world` is a fixture
    _Venue, _close_row, _closer, _set_rows, _use, world,
)


def _assert_accounted(s):
    outcomes = {k: v for k, v in s.items()
                if k not in om._EQUITY_SWEEP_NON_OUTCOME_KEYS and v}
    assert s["unaccounted"] == 0, (s["checked"], outcomes)
    assert om._equity_sweep_row_outcomes(s) == s["checked"], outcomes


_OLD = "2026-09-18T16:00:00+00:00"


def test_live_line_2026_09_29_nine_rows_all_accounted(world):  # noqa: F811 — pytest fixture
    """The 18:55:56Z shape: 7 rows with a resting stop, one active-close skip,
    one inside the fresh-fill grace. Before this change the sum was 7 of 9."""
    db, _pages, mp = world
    fresh = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    rows = [(i, "alpaca_portfolio", f"S{i}", "long", 10.0, 90.0, 110.0, _OLD)
            for i in range(1, 8)]
    rows += [(8, "alpaca_portfolio", "GLD", "long", 5.0, 300.0, 400.0, _OLD),
             (9, "alpaca_portfolio", "S9", "long", 3.0, 90.0, 110.0, fresh)]
    _set_rows(db, rows)
    mp.setattr(om, "is_active_close", lambda acc, sym: sym == "GLD")

    class _Covered(_Venue):
        def protection_state(self, symbol, since=None):
            return {"stop": True, "target": True, "legs": 2}

        def positions(self):
            return [{"symbol": f"S{i}", "qty": "10", "side": "long"} for i in range(1, 8)]

        def protection_coverage(self, symbol, position=None, since=None):
            return {"size": 10.0, "side": "buy", "stop_qty": 10.0, "target_qty": 10.0,
                    "legs": 2, "unknown_qty_legs": 0, "source": "orders",
                    "stop_leg_qtys": [10.0]}

    _use(mp, _Covered())
    s = om._check_broker_naked_equity_positions(db)
    assert s["checked"] == 9 and s["covered"] == 7
    assert s["stop_resting"] == 7
    assert s["active_close_skipped"] == 1 and s["grace_skipped"] == 1
    _assert_accounted(s)


def test_no_client_and_empty_symbol_are_counted(world):  # noqa: F811 — pytest fixture
    db, _pages, mp = world
    _set_rows(db, [(1, "alpaca_portfolio", "", "long", 1.0, 90.0, 110.0, _OLD),
                   (2, "alpaca_portfolio", "QQQ", "long", 1.0, 90.0, 110.0, _OLD)])
    mp.setattr("src.units.accounts.clients.alpaca_client_for", lambda acc: None)
    s = om._check_broker_naked_equity_positions(db)
    assert s["no_symbol"] == 1 and s["no_client"] == 1
    _assert_accounted(s)


def test_no_levels_is_counted(world):  # noqa: F811 — pytest fixture
    db, _pages, mp = world
    _set_rows(db, [(6024, "alpaca_portfolio", "QQQ", "long", 56.0, None, None, _OLD)])
    mp.setattr(om, "_resolve_protective_levels", lambda *a, **k: (None, None))
    _use(mp, _Venue(qty=56.0, price=735.0))
    s = om._check_broker_naked_equity_positions(db)
    assert s["no_levels"] == 1
    _assert_accounted(s)


def test_replay_2026_09_24_every_sweep_is_accounted(world):  # noqa: F811 — pytest fixture
    """30 sweeps of the measured loop: refusal, re-arm, then a resting stop.
    No sweep leaves a row in no counter or in two."""
    db, _pages, mp = world
    _use(mp, _Venue(qty=56.0, price=735.0))
    for _ in range(30):
        _assert_accounted(om._check_broker_naked_equity_positions(db))


def test_deferred_exit_that_rests_a_stop_is_one_outcome_not_two(world):  # noqa: F811 — pytest fixture
    """A deferred exit that puts a stop back to rest counted exit_deferred AND
    rearmed for one row. `rearmed` is now a separate sub-count."""
    db, _pages, mp = world
    _close_row(db, 5928)
    _use(mp, _Venue(qty=56.0, price=710.0))
    mp.setattr(om, "_send_close_to_exchange",
               lambda m: {"ok": False, "error": "market closed — exit deferred"})
    mp.setattr(om, "_attempt_naked_autoprotect", lambda row, sl, tp, db=None: True)
    s = om._check_broker_naked_equity_positions(db)
    assert s["exit_deferred"] == 1 and s["exit_deferred_rearmed"] == 1
    assert s["rearmed"] == 0
    _assert_accounted(s)


def test_venue_holding_second_row_is_counted_as_duplicate(world):  # noqa: F811 — pytest fixture
    db, _pages, mp = world
    _set_rows(db, [
        (1, "alpaca_portfolio", "QQQ", "long", 10.0, 700.0, 790.0, _OLD),
        (2, "alpaca_portfolio", "QQQ", "long", 10.0, 725.0, 780.0, _OLD),
        (3, "alpaca_portfolio", "QQQ", "long", 20.0, 710.0, 785.0, _OLD),
    ])
    # the accepted OCO is not yet visible to this sweep's reads, so rows 2 and
    # 3 reach the venue-holding branch after row 1 already handled it
    v = _Venue(qty=30.0, price=735.0)
    _orig = v._request

    def _req(method, path, json_body=None):
        if method == "POST":
            v.calls.append((method, path, json_body))
            return {"retCode": 0, "result": {"id": "oco-venue"}}
        return _orig(method, path, json_body)
    v._request = _req
    _use(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert len(v.posts()) == 1
    assert s["venue_holding_protected"] == 1
    assert s["venue_holding_duplicate"] == 2
    _assert_accounted(s)


def test_non_escalated_failed_rearm_is_counted(world):  # noqa: F811 — pytest fixture
    db, _pages, mp = world
    _close_row(db, 5928)

    class _Reject(_Venue):
        def _request(self, method, path, json_body=None):  # type: ignore[override]
            if method == "POST":
                self.calls.append((method, path, json_body))
                return {"retCode": 422, "retMsg": "refused"}
            return super()._request(method, path, json_body)

    _use(mp, _Reject(qty=56.0, price=735.0))
    s = om._check_broker_naked_equity_positions(db)
    assert s["rearm_failed"] == 1 and s["rearmed"] == 0
    _assert_accounted(s)


def _escalating(db, mp):
    _close_row(db, 5928)
    om._ALPACA_BREACH_DEFERRALS[("alpaca_portfolio", "QQQ", 6024)] = om._BREACH_DEFER_MAX
    v = _Venue(qty=56.0, price=720.0)
    v.bid, v.ask = 716.50, 720.10
    v.trade_age = None
    _use(mp, v)
    return v


def test_escalated_post_that_never_reached_the_venue_is_not_a_rejection(world):  # noqa: F811 — pytest fixture
    """(b) An escalated re-arm that fails before any venue call counts as
    escalated_post_not_sent, not escalated_post_rejected. It still spends one
    unit of the cap: nothing rests either way, and the cap is what bounds the
    row's path to the close."""
    db, _pages, mp = world
    v = _escalating(db, mp)
    mp.setattr(om, "_attempt_naked_autoprotect",
               lambda row, sl, tp, db=None, trace=None: False)
    s = om._check_broker_naked_equity_positions(db)
    assert v.posts() == []
    assert s["escalated_post_not_sent"] == 1 and s["escalated_post_rejected"] == 0
    assert om._rearm_attempts("alpaca_portfolio", 6024) == 1
    _assert_accounted(s)


def test_escalated_post_the_venue_refused_is_a_rejection(world):  # noqa: F811 — pytest fixture
    db, _pages, mp = world
    v = _escalating(db, mp)

    def _req(method, path, json_body=None, _orig=v._request):
        if method == "POST":
            v.calls.append((method, path, json_body))
            return {"retCode": 422, "retMsg": "stop price must be below market"}
        return _orig(method, path, json_body)
    v._request = _req
    s = om._check_broker_naked_equity_positions(db)
    assert s["escalated_post_rejected"] == 1 and s["escalated_post_not_sent"] == 0
    _assert_accounted(s)


def test_trace_marks_a_venue_call_only_when_one_was_made(world):  # noqa: F811 — pytest fixture
    db, _pages, mp = world
    _use(mp, _Venue(qty=56.0, price=735.0))
    row = {"id": 6024, "account_id": "alpaca_portfolio", "symbol": "QQQ",
           "direction": "long", "position_size": 56.0}
    t: dict = {}
    assert om._attempt_naked_autoprotect(row, 716.8, 787.86, db=db, trace=t) is True
    assert t == {"sent": True, "responded": True, "ret_code": 0}
    t2: dict = {}
    mp.setattr("src.units.accounts.clients.alpaca_client_for", lambda acc: None)
    assert om._attempt_naked_autoprotect(row, 716.8, 787.86, db=db, trace=t2) is False
    assert "sent" not in t2


def test_rearm_cap_is_three_and_bounds_a_stop_that_never_holds(world):  # noqa: F811 — pytest fixture
    """PR6YRTQY-0005 (d), pinned to a NUMBER. The row's shares stay present and
    the price sits above its stop, but every re-armed stop vanishes (the venue
    accepts it and nothing rests). The row is re-armed exactly 3 times, then
    escalated to the trade-scoped close on the 4th sweep. The 28 is only an
    upper bound on the loop, which stops at the close; it matches the
    measured 2026-09-24 storm size. The existing tests
    read om._ALPACA_REARM_CAP, so they passed unchanged with the cap at 50."""
    assert om._ALPACA_REARM_CAP == 3
    db, _pages, mp = world
    _close_row(db, 5928)

    class _Vanishing(_Venue):
        def _request(self, method, path, json_body=None):  # type: ignore[override]
            if method == "POST":
                self.calls.append((method, path, json_body))
                return {"retCode": 0, "result": {"id": f"oco-{len(self.calls)}"}}
            return super()._request(method, path, json_body)

    v = _Vanishing(qty=56.0, price=735.0)
    _use(mp, v)
    closes = _closer(mp, v)
    for _ in range(28):
        s = om._check_broker_naked_equity_positions(db)
        _assert_accounted(s)
        if closes:
            break
    assert len(v.posts()) == 3
    assert [c["id"] for c in closes] == [6024]
    assert db.status(6024) == ("closed", "protection_rearm_exhausted")


def test_topup_deferred_comment_matches_the_cooldown(world):  # noqa: F811 — pytest fixture
    """(a) The refused resting top-up sets the 900 s cooldown, so the next
    sweep does not retry. The old comment said it did."""
    src = (Path(__file__).resolve().parents[1] / "src" / "runtime"
           / "order_monitor.py").read_text()
    assert "no cooldown was left" not in src


def test_a_raising_escalated_post_is_not_counted_as_a_venue_rejection(world):  # noqa: F811 — pytest fixture
    """Manager review of #14545, note (a): `sent` is set before the client call,
    so a call that RAISES must not be read as a venue rejection."""
    db, _pages, mp = world
    v = _escalating(db, mp)

    def _boom(order):
        raise ConnectionError("reset by peer")
    v.place_protective = _boom
    s = om._check_broker_naked_equity_positions(db)
    assert s["escalated_post_not_sent"] == 1 and s["escalated_post_rejected"] == 0
    assert om._rearm_attempts("alpaca_portfolio", 6024) == 1
    _assert_accounted(s)
