"""Tests for the prop account-status request ping.

Covers the trigger condition (an absent/stale ``prop_account_status`` snapshot
on a declared prop account — **with or without an open position**), the
freshness gate, the cooldown, the pause knob, cadence-state pruning, and the
reply-template content — all against an isolated ``trade_journal.db`` +
runtime-logs dir with the notification emitter monkeypatched (no FCM /
Telegram I/O).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


@pytest.fixture
def declared(monkeypatch: pytest.MonkeyPatch):
    """Pin the declared prop-account set so tests don't ride real config.

    Returns a setter; call it with the ids a test wants declared, or with
    ``None`` to simulate an unreadable ``accounts.yaml``. Left at the default
    ``["breakout_1"]`` when a test doesn't care.
    """
    def _set(ids):
        from src.prop import prop_status_request

        monkeypatch.setattr(prop_status_request, "declared_prop_account_ids",
                            lambda *, live_only=False: ids)

    _set(["breakout_1"])
    return _set


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    """Capture emit_prop_status_request calls instead of sending."""
    calls: List[Dict[str, Any]] = []

    def _fake(account_id: str, open_positions, *, age_hours=None,
              push: bool = True, telegram: bool = True):
        calls.append({"account_id": account_id,
                      "open_positions": open_positions,
                      "age_hours": age_hours})
        return {"push": True, "telegram": True}

    from src.prop import breakout_notify

    monkeypatch.setattr(breakout_notify, "emit_prop_status_request", _fake)
    return calls


def _open_fill() -> Dict[str, Any]:
    return {
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "ETHUSDT", "direction": "long", "qty": 1.87,
        "entry_price": 1613.78, "status": "filled",
    }


def test_pings_when_no_snapshot_ever(isolated_env: Path, captured) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    pinged = run_prop_status_request()
    assert pinged == ["breakout_1"]
    assert captured[0]["age_hours"] is None  # never reported


def test_fresh_snapshot_suppresses(isolated_env: Path, captured) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    prop_journal.insert_account_status({
        "account_id": "breakout_1", "balance": 5040, "equity": 5010,
    })
    assert run_prop_status_request() == []
    assert captured == []


def test_cooldown_prevents_nagging(isolated_env: Path, captured) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    assert run_prop_status_request() == ["breakout_1"]
    # immediate second tick: still stale, but inside the cooldown
    assert run_prop_status_request() == []
    assert len(captured) == 1


def test_reasks_after_cooldown(isolated_env: Path, captured) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    now = datetime.now(timezone.utc)
    assert run_prop_status_request(now=now) == ["breakout_1"]
    later = now + timedelta(hours=13)  # past the 12h default cooldown
    assert run_prop_status_request(now=later) == ["breakout_1"]
    assert len(captured) == 2


def test_flat_declared_account_is_still_asked(isolated_env: Path, captured,
                                              declared) -> None:
    """A FLAT prop account with no snapshot must still be asked.

    This test used to assert the exact opposite — ``run_prop_status_request()
    == []`` and the cadence state pruned to ``{}`` when nothing was open — and
    it passed against a real defect. The old implementation bailed on
    ``if not positions: return []``, so the instant the prop book went flat the
    bot stopped asking and the snapshot aged without bound.

    That is wrong because the two prop limits are not both position-scoped:
    ``config/prop_rulesets/breakout.yaml`` declares ``drawdown_type: static``
    with ``max_drawdown_pct: 0.06`` on a ``$5,000`` account — a **$4,700
    account-level floor** that binds while flat. A flat account is precisely
    when the next ticket is about to be sized against a cushion nobody has
    measured. Do not restore the old assertion.
    """
    from src.prop.prop_status_request import run_prop_status_request, _load_state

    assert run_prop_status_request() == ["breakout_1"]
    assert len(captured) == 1
    # Flat is stated, not implied: `[]` (looked, book is empty), never `None`.
    assert captured[0]["open_positions"] == []
    # And the cadence state SURVIVES, or the cooldown cannot bound the re-ask.
    assert "breakout_1" in _load_state()


def test_undeclared_flat_account_is_not_asked(isolated_env: Path, captured,
                                              declared) -> None:
    """Widening the trigger must not turn into asking about everything.

    The ask is scoped to declared prop accounts (union open positions). With
    nothing declared and nothing open there is no one to ask, and the state
    prunes — which is what keeps a retired account from nagging forever.
    """
    from src.prop.prop_status_request import run_prop_status_request, _load_state

    declared([])
    assert run_prop_status_request() == []
    assert captured == []
    assert _load_state() == {}


def test_position_holder_asked_even_when_not_declared(isolated_env: Path,
                                                      captured, declared) -> None:
    """A position we can see is a position to protect.

    An id that config no longer declares but that still holds an open prop
    position is covered by the union — otherwise removing a line from
    ``accounts.yaml`` would silently blind the guard on a live position.
    """
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    declared([])
    prop_journal.insert_fill(_open_fill())
    assert run_prop_status_request() == ["breakout_1"]


def test_unreadable_config_still_covers_open_positions(isolated_env: Path,
                                                       captured, declared) -> None:
    """``None`` from the enumerator is "we could not look", not "none exist".

    Coverage degrades to the position-holding subset — strictly smaller, and
    logged — rather than silently becoming zero.
    """
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    declared(None)
    prop_journal.insert_fill(_open_fill())
    assert run_prop_status_request() == ["breakout_1"]


def test_failed_position_scan_is_not_reported_as_flat(
        isolated_env: Path, captured, declared,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A scan failure must reach the operator as ``None``, never ``[]``.

    "We could not read your positions" and "you hold nothing" are opposite
    statements, and the ping renders them differently.
    """
    from src.prop import prop_monitor_pulse
    from src.prop.prop_status_request import run_prop_status_request

    def _boom():
        raise RuntimeError("journal unreadable")

    monkeypatch.setattr(prop_monitor_pulse, "find_open_prop_positions", _boom)

    assert run_prop_status_request() == ["breakout_1"]
    assert captured[0]["open_positions"] is None


def test_real_config_declares_the_live_prop_account() -> None:
    """Positive control: the enumerator finds a real account in real config.

    Every other test here pins the declared set, so without this one a broken
    enumerator returning ``[]`` against production config would leave the whole
    file green — the unasserted-denominator shape.
    """
    from src.prop.prop_identity import declared_prop_account_ids

    ids = declared_prop_account_ids(live_only=True)
    assert ids is not None, "accounts.yaml must be readable from the repo root"
    assert "breakout_1" in ids


def test_pause_knob(isolated_env: Path, captured,
                    monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    monkeypatch.setenv("PROP_STATUS_REQUEST_MAX_AGE_HOURS", "0")
    assert run_prop_status_request() == []
    assert captured == []


def test_template_contains_both_reply_formats(isolated_env: Path) -> None:
    """The ping body must carry the exact formats the report handler parses."""
    from src.prop.breakout_notify import render_status_request_message

    text = render_status_request_message(
        "breakout_1",
        [{"symbol": "ETHUSDT", "direction": "long", "qty": 1.87,
          "entry_price": 1613.78}],
        age_hours=None,
    )
    assert "bal <balance> <equity>" in text
    assert '"kind":"account_status"' in text
    assert '"account_id":"breakout_1"' in text
    assert "ETHUSDT" in text


def test_stale_snapshot_reasks(isolated_env: Path, captured,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    prop_journal.insert_account_status({
        "account_id": "breakout_1", "balance": 5040, "equity": 5010,
    })
    # a 1h-max-age knob makes the just-written snapshot stale 2h from now
    monkeypatch.setenv("PROP_STATUS_REQUEST_MAX_AGE_HOURS", "1")
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert run_prop_status_request(now=later) == ["breakout_1"]
    assert captured[0]["age_hours"] is not None
    assert captured[0]["age_hours"] >= 1.9


# ── MI-214: the trigger is ACTIVITY, not the wall clock ──────────────────
# Operator, 2026-09-09, after being pinged about `breakout_1` with no open
# position and a 230h snapshot: "This ping in the prop channel is unnecessary,
# the account snapshot is updated when a trade closes, if there are no prop
# trades than the account state didn't change".
#
# These lock the QUIESCENT verdict — the only one that suppresses the ask —
# and, just as importantly, lock the four cases that must STILL ask.


def _status_row(hours_ago: float = 0.0) -> Dict[str, Any]:
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"account_id": "breakout_1", "balance": 4787.34,
            "equity": 4787.34, "reported_at": ts.isoformat()}


def _ticket(status: str, hours_ago: float) -> Dict[str, Any]:
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"ticket_id": f"prop-{status}-{hours_ago}", "account_id": "breakout_1",
            "symbol": "ETHUSDT", "direction": "long", "status": status,
            "created_at": ts.isoformat()}


def test_quiescent_flat_account_with_stale_snapshot_is_not_asked() -> None:
    """THE HEADLINE REGRESSION — the exact live shape the operator complained about.

    `breakout_1` on 2026-09-09: snapshot 230h old, book FLAT, and the only
    tickets since it are `suppressed`. Nothing has happened, so nothing is
    asked. Before MI-214 this fired on wall-clock age alone.
    """
    from src.prop.prop_status_request import QUIESCENT, assess_activity

    verdict, detail = assess_activity(
        snapshot_reported_at=_status_row(230.0)["reported_at"],
        open_positions=[],
        tickets=[_ticket("suppressed", 96.0), _ticket("suppressed", 95.0),
                 _ticket("closed", 240.0)],
    )
    assert verdict == QUIESCENT
    assert detail["open_positions"] == 0
    assert detail["tickets_since_snapshot"] == 0


def test_a_reported_fill_since_the_snapshot_is_not_by_itself_a_trigger() -> None:
    """A reported fill can only make the cushion CONSERVATIVE, so it is not a
    reason to ask.

    `prop_reconcile.reconstruct_equity` is deliberately asymmetric: an
    unplaceable LOSS is always applied, an unplaceable GAIN is withheld and
    counted. The dangerous direction is closed by construction. Live instance:
    `breakout_1` fill id 41 (+35.28, 2026-08-30T19:39Z) is 5.5 min NEWER than
    the snapshot it supposedly produced and is a withheld gain — the panel
    shows an $87.34 cushion where the true one is ~$122.62.

    `assess_activity` takes no fills at all, and that is the assertion: fills
    are not an input to this decision. If a future change adds them, this test
    should be the thing that argues about it.
    """
    import inspect

    from src.prop.prop_status_request import assess_activity

    params = set(inspect.signature(assess_activity).parameters)
    assert "fills" not in params, (
        "fills must not become a trigger input without revisiting "
        "reconstruct_equity's asymmetry — see the module docstring")


def test_open_position_asks_even_though_the_book_reported_nothing() -> None:
    """The one genuinely UNSAFE case: unrealized drawdown is invisible.

    Nothing reports an open position's drawdown, so it can breach the static
    DD floor with no fill ever being written. This must outrank quiescence.
    """
    from src.prop.prop_status_request import ASK_POSITION_OPEN, assess_activity

    verdict, detail = assess_activity(
        snapshot_reported_at=_status_row(230.0)["reported_at"],
        open_positions=[{"symbol": "ETHUSDT", "qty": 1.87}],
        tickets=[],
    )
    assert verdict == ASK_POSITION_OPEN
    assert detail["open_positions"] == 1


def test_real_ticket_since_snapshot_asks_preserving_the_2026_08_14_purpose() -> None:
    """New exposure taken against an unmeasured cushion must still ask.

    This is the 2026-08-14 Tier-2 concern — "flat is exactly when the next
    ticket is sized against a cushion nobody has measured" — now OBSERVED
    rather than assumed. The flat bail is NOT restored; a flat account with a
    real ticket since the snapshot is asked.
    """
    from src.prop.prop_status_request import (
        ASK_TICKET_SINCE_SNAPSHOT, assess_activity)

    verdict, detail = assess_activity(
        snapshot_reported_at=_status_row(100.0)["reported_at"],
        open_positions=[],
        tickets=[_ticket("emitted", 2.0)],
    )
    assert verdict == ASK_TICKET_SINCE_SNAPSHOT
    assert detail["tickets_since_snapshot"] == 1


def test_suppressed_ticket_takes_no_exposure_so_it_does_not_ask() -> None:
    """`suppressed` is journaled by breakout_executor and returns BEFORE
    routing, so it never reaches a venue and never becomes a position.
    Counting it would recreate the wall-clock noise with no safety gain."""
    from src.prop.prop_status_request import QUIESCENT, assess_activity

    verdict, _ = assess_activity(
        snapshot_reported_at=_status_row(100.0)["reported_at"],
        open_positions=[],
        tickets=[_ticket("suppressed", 2.0)],
    )
    assert verdict == QUIESCENT


def test_ticket_predating_the_snapshot_does_not_ask() -> None:
    from src.prop.prop_status_request import QUIESCENT, assess_activity

    verdict, _ = assess_activity(
        snapshot_reported_at=_status_row(10.0)["reported_at"],
        open_positions=[],
        tickets=[_ticket("emitted", 50.0)],
    )
    assert verdict == QUIESCENT


def test_undateable_ticket_counts_as_exposing() -> None:
    """Fail-SAFE, the opposite polarity to an order gate: this decides whether
    an ALARM fires, so an unreadable row must ask, never suppress."""
    from src.prop.prop_status_request import (
        ASK_TICKET_SINCE_SNAPSHOT, assess_activity)

    verdict, _ = assess_activity(
        snapshot_reported_at=_status_row(10.0)["reported_at"],
        open_positions=[],
        tickets=[{"ticket_id": "x", "status": "emitted", "created_at": None}],
    )
    assert verdict == ASK_TICKET_SINCE_SNAPSHOT


def test_unrecognised_ticket_status_counts_as_exposing() -> None:
    """Only `suppressed` is known to take no exposure. A status this module has
    never seen must not be assumed harmless."""
    from src.prop.prop_status_request import (
        ASK_TICKET_SINCE_SNAPSHOT, assess_activity)

    verdict, _ = assess_activity(
        snapshot_reported_at=_status_row(10.0)["reported_at"],
        open_positions=[],
        tickets=[_ticket("some_future_status", 1.0)],
    )
    assert verdict == ASK_TICKET_SINCE_SNAPSHOT


def test_unreadable_positions_is_unknown_never_quiescent() -> None:
    """"We could not look" and "nothing happened" are opposite statements.

    `find_open_prop_positions` swallows its own `list_fills` failure into `[]`,
    so this module must not treat an empty read as evidence of quiescence when
    the read itself is in doubt.
    """
    from src.prop.prop_status_request import UNKNOWN, assess_activity

    verdict, detail = assess_activity(
        snapshot_reported_at=_status_row(230.0)["reported_at"],
        open_positions=None, tickets=[],
    )
    assert verdict == UNKNOWN
    assert detail["reason"] == "positions_unreadable"


def test_unreadable_tickets_is_unknown_never_quiescent() -> None:
    from src.prop.prop_status_request import UNKNOWN, assess_activity

    verdict, detail = assess_activity(
        snapshot_reported_at=_status_row(230.0)["reported_at"],
        open_positions=[], tickets=None,
    )
    assert verdict == UNKNOWN
    assert detail["reason"] == "tickets_unreadable"


def test_missing_or_undateable_snapshot_asks_never_quiescent() -> None:
    """With no anchor, "since the snapshot" is undefined — quiescence cannot be
    established, and with no cushion at all the account cannot size."""
    from src.prop.prop_status_request import ASK_NO_SNAPSHOT, assess_activity

    for anchor in (None, "", "not-a-timestamp"):
        verdict, _ = assess_activity(
            snapshot_reported_at=anchor, open_positions=[], tickets=[])
        assert verdict == ASK_NO_SNAPSHOT, anchor


def test_every_declared_activity_state_is_reachable() -> None:
    """A state nothing can emit is already collapsed
    (docs/CLAUDE-RULES-CANONICAL.md § "Collapsed states")."""
    from src.prop import prop_status_request as m

    fresh = _status_row(1.0)["reported_at"]
    emitted = {
        m.assess_activity(snapshot_reported_at=None,
                          open_positions=[], tickets=[])[0],
        m.assess_activity(snapshot_reported_at=fresh,
                          open_positions=[{"qty": 1}], tickets=[])[0],
        m.assess_activity(snapshot_reported_at=fresh, open_positions=[],
                          tickets=[_ticket("emitted", 0.0)])[0],
        m.assess_activity(snapshot_reported_at=fresh,
                          open_positions=[], tickets=[])[0],
        m.assess_activity(snapshot_reported_at=fresh,
                          open_positions=None, tickets=None)[0],
    }
    assert emitted == set(m.ACTIVITY_STATES)


def test_end_to_end_quiescent_account_is_not_pinged(
    isolated_env: Path, captured, declared,
) -> None:
    """The whole path, not just the pure function: a stale snapshot on a flat
    account with a closed fill produces NO ping.

    The fill is `closed`, so the book is flat while the fill stream is
    non-empty — which is the live `breakout_1` shape and the case a naive
    "any fill since the snapshot" trigger would get wrong.
    """
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1", "symbol": "SOLUSDT",
        "direction": "long", "qty": 49.0, "entry_price": 105.04,
        "exit_price": 105.76, "pnl": 35.28, "status": "closed",
    })
    prop_journal.insert_account_status({
        "account_id": "breakout_1", "balance": 4787.34, "equity": 4787.34})
    monkey_age = "1"
    import os
    os.environ["PROP_STATUS_REQUEST_MAX_AGE_HOURS"] = monkey_age
    try:
        later = datetime.now(timezone.utc) + timedelta(hours=230)
        assert run_prop_status_request(now=later) == []
        assert captured == []
    finally:
        os.environ.pop("PROP_STATUS_REQUEST_MAX_AGE_HOURS", None)


def test_end_to_end_open_position_still_pinged_when_stale(
    isolated_env: Path, captured, declared,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The positive control for the test above: same stale snapshot, but the
    book is OPEN — so the ping still fires. Without this, a quiescent-verdict
    bug that silenced everything would look identical to a working filter."""
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    prop_journal.insert_account_status({
        "account_id": "breakout_1", "balance": 4787.34, "equity": 4787.34})
    monkeypatch.setenv("PROP_STATUS_REQUEST_MAX_AGE_HOURS", "1")
    later = datetime.now(timezone.utc) + timedelta(hours=230)
    assert run_prop_status_request(now=later) == ["breakout_1"]
    assert len(captured) == 1
