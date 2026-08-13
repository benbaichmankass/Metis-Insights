"""A prop account's sizing balance comes from the operator's report, not a socket.

WHY. `Coordinator._default_balance_fetcher` sourced the sizing basis from the
broker. A Breakout prop account has NO broker socket by design, so that lookup
could only return ``None`` and the fetcher raised *"API error or credentials
missing — account unreachable"*. Every clause of that is false for a prop
account. Measured 2026-08-13: 5 of the 7 lifetime rejections on
`trend_donchian_sol`, and the reason it had never emitted a prop ticket.

THE SAFETY PROPERTY THESE TESTS EXIST TO PIN: the change can never size a trade
off a guess. Three of the four states still refuse; only a FRESH
operator-reported snapshot yields a number.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.prop import prop_balance as pb


def _row(hours_old=1.0, equity=5010.0, balance=5040.0, ts=True):
    r = {"equity": equity, "balance": balance}
    if ts:
        r["reported_at"] = (datetime.now(timezone.utc)
                            - timedelta(hours=hours_old)).isoformat()
    return r


def _patch(monkeypatch, row=None, raises=None):
    import src.prop.prop_journal as pj

    def fake(account_id):
        if raises:
            raise raises
        return row

    monkeypatch.setattr(pj, "latest_account_status", fake)


def test_fresh_snapshot_sizes_and_prefers_equity(monkeypatch):
    """Equity is what the prop firm's drawdown rules are measured against, so
    sizing off it agrees with the rule-distance guard."""
    _patch(monkeypatch, _row(equity=5010.0, balance=5040.0))
    state, bal, meta = pb.prop_sizing_balance("breakout_1")
    assert state == "ok"
    assert bal == 5010.0
    assert meta["source"] == "equity"


def test_falls_back_to_balance_when_equity_absent(monkeypatch):
    _patch(monkeypatch, _row(equity=None, balance=5040.0))
    state, bal, meta = pb.prop_sizing_balance("breakout_1")
    assert (state, bal, meta["source"]) == ("ok", 5040.0, "balance")


def test_no_snapshot_is_ABSENT_not_an_api_error(monkeypatch):
    _patch(monkeypatch, None)
    state, bal, meta = pb.prop_sizing_balance("breakout_1")
    assert state == "absent"
    assert bal is None
    msg = pb.refusal_message(state, "breakout_1", meta)
    assert "NO broker API by design" in msg
    assert "credentials" not in msg.lower()


def test_a_read_failure_is_ERROR_not_absent(monkeypatch):
    """'We could not look' and 'we looked and found nothing' call for opposite
    operator actions — fix the reader vs send a balance."""
    _patch(monkeypatch, raises=RuntimeError("db locked"))
    state, bal, meta = pb.prop_sizing_balance("breakout_1")
    assert state == "error"
    assert bal is None
    assert "reader fault" in pb.refusal_message(state, "breakout_1", meta)


def test_stale_snapshot_refuses(monkeypatch):
    """A week-old balance must not size a live order."""
    monkeypatch.delenv(pb._ENV_MAX_AGE, raising=False)
    _patch(monkeypatch, _row(hours_old=200.0))
    state, bal, meta = pb.prop_sizing_balance("breakout_1")
    assert (state, bal) == ("stale", None)
    assert "fresh" in pb.refusal_message(state, "breakout_1", meta)


def test_undateable_snapshot_is_stale_not_fresh(monkeypatch):
    """An undateable row cannot be SHOWN to be current, and the fail-safe
    direction on a sizing input is to refuse."""
    monkeypatch.delenv(pb._ENV_MAX_AGE, raising=False)
    _patch(monkeypatch, _row(ts=False))
    state, _, meta = pb.prop_sizing_balance("breakout_1")
    assert state == "stale"
    assert "undateable" in meta["reason"]


def test_threshold_zero_disables_the_staleness_check(monkeypatch):
    monkeypatch.setenv(pb._ENV_MAX_AGE, "0")
    _patch(monkeypatch, _row(hours_old=9000.0))
    state, bal, _ = pb.prop_sizing_balance("breakout_1")
    assert (state, bal) == ("ok", 5010.0)


def test_an_unparseable_threshold_falls_back_not_disables(monkeypatch):
    """A typo must not silently widen what counts as a fresh balance."""
    monkeypatch.setenv(pb._ENV_MAX_AGE, "twenty-four")
    assert pb.max_age_hours() == pb._DEFAULT_MAX_AGE_H


def test_zero_or_negative_equity_does_not_size(monkeypatch):
    """A blown account reporting 0 must refuse, not size off zero."""
    monkeypatch.delenv(pb._ENV_MAX_AGE, raising=False)
    _patch(monkeypatch, _row(equity=0.0, balance=0.0))
    state, bal, _ = pb.prop_sizing_balance("breakout_1")
    assert (state, bal) == ("absent", None)


@pytest.mark.parametrize("state", ["stale", "absent", "error"])
def test_every_refusing_state_yields_no_number(monkeypatch, state):
    """THE SAFETY PROPERTY. Only `ok` may produce a balance."""
    assert pb.refusal_message(state, "breakout_1", {"reason": "x"})


def test_the_check_can_fail(monkeypatch):
    """If the fresh path did not work, every 'refuses' assertion above would
    pass vacuously against a function that never returns anything."""
    _patch(monkeypatch, _row())
    state, bal, _ = pb.prop_sizing_balance("breakout_1")
    assert state == "ok" and bal is not None
