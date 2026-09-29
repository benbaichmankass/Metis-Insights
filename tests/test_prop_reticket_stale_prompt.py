"""PI-20260929-2PNSPDNU-0002: a prompt nobody answered is not an outstanding ticket.

``breakout_executor._reticket_suppress_reason`` treated a ticket left at
``expiry_prompted`` / ``awaiting_report`` as OUTSTANDING with no look at its
``valid_until``, although its own docstring said an expired unacted ticket
does not block. Measured 2026-09-29 (api/bot/prop/tickets, #14291): the two
09-25 11:11Z tickets prop-manual-2209e50e9bfc (SOL long) and
prop-manual-693bb30f7638 (ETH long), valid until 12:11Z, expiry prompt never
answered, suppressed the 09-27 07:58Z / 08:01Z SOL longs and the 09-28 17:15Z
ETH long — the same failure class as 2026-09-11, when two 08-28 / 08-30
prompts had suppressed 37 signals until cleared by hand (fills #42 / #43).

Now: ``placed`` (a working order) and ``awaiting_report`` (the operator said
"yes, placed" and never reported — possibly a live position the fills journal
cannot see) block until reported, with no time window (manager decision
2026-09-29 17:24Z); ``expiry_prompted`` (the prompt was never answered, so
nothing is known to have been placed) blocks only within ``valid_until`` +
``STALE_PROMPT_GRACE``, or with no readable validity (fail-safe); ``emitted``
blocks only within ``valid_until`` (unchanged). Every case here runs against
an isolated ``trade_journal.db`` through the real journal read.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

#: The live case, to the minute: the 09-25 ticket and the 09-27 signal it blocked.
EMITTED_AT = datetime(2026, 9, 25, 11, 11, 29, tzinfo=timezone.utc)
VALID_UNTIL = EMITTED_AT + timedelta(hours=1)
SIGNAL_0927 = datetime(2026, 9, 27, 7, 58, 54, tzinfo=timezone.utc)


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


def _ticket(ticket_id: str, *, status: str, valid_until: datetime | None | str = VALID_UNTIL,
            symbol: str = "SOLUSDT", direction: str = "long") -> None:
    from src.prop import prop_journal

    prop_journal.record_ticket({
        "ticket_id": ticket_id, "account_id": "breakout_1",
        "strategy": "trend_donchian_sol_prop", "symbol": symbol,
        "direction": direction, "side": "Buy",
        "entry": 121.57, "sl": 118.70, "tp": 133.61, "qty": 26.14, "risk_usd": 75.0,
        "signal_time": EMITTED_AT.isoformat(),
        "valid_until": (valid_until.isoformat() if isinstance(valid_until, datetime)
                        else valid_until),
        "status": status,
    })


def _reason(now: datetime, symbol: str = "SOLUSDT", direction: str = "long"):
    from src.prop.breakout_executor import _reticket_suppress_reason

    return _reticket_suppress_reason("breakout_1", symbol, direction, now=now)


# ── the negative control: the probe can still find a positive ────────────

def test_a_prompt_inside_its_grace_window_still_blocks(isolated_env):
    from src.prop.breakout_executor import STALE_PROMPT_GRACE

    _ticket("prop-manual-2209e50e9bfc", status="expiry_prompted")
    just_inside = VALID_UNTIL + STALE_PROMPT_GRACE - timedelta(minutes=1)
    assert _reason(VALID_UNTIL + timedelta(hours=1)) == \
        "outstanding_ticket:expiry_prompted: prop-manual-2209e50e9bfc"
    assert _reason(just_inside) == \
        "outstanding_ticket:expiry_prompted: prop-manual-2209e50e9bfc"


# ── the fix ──────────────────────────────────────────────────────────────

def test_the_0925_prompt_no_longer_blocks_the_0927_signal(isolated_env):
    # REGRESSION, the live case: valid until 09-25 12:11Z, prompt never
    # answered, signal at 09-27 07:58Z (~44h later) — was suppressed
    _ticket("prop-manual-2209e50e9bfc", status="expiry_prompted")
    assert _reason(SIGNAL_0927) is None


def test_a_prompt_three_days_past_its_validity_no_longer_blocks(isolated_env):
    _ticket("prop-manual-2209e50e9bfc", status="expiry_prompted")
    assert _reason(VALID_UNTIL + timedelta(days=3)) is None


def test_the_window_edge_is_exact(isolated_env):
    from src.prop.breakout_executor import STALE_PROMPT_GRACE

    _ticket("prop-manual-2209e50e9bfc", status="expiry_prompted")
    edge = VALID_UNTIL + STALE_PROMPT_GRACE
    assert _reason(edge - timedelta(seconds=1)) is not None
    assert _reason(edge) is None                       # `>` not `>=`: at the edge it is stale


def test_the_eth_prompt_follows_the_same_rule(isolated_env):
    _ticket("prop-manual-693bb30f7638", status="expiry_prompted", symbol="ETHUSDT")
    assert _reason(VALID_UNTIL + timedelta(hours=2), symbol="ETHUSDT") == \
        "outstanding_ticket:expiry_prompted: prop-manual-693bb30f7638"
    assert _reason(VALID_UNTIL + timedelta(days=2), symbol="ETHUSDT") is None


# ── what does NOT change ─────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["placed", "awaiting_report"])
def test_a_working_order_or_a_yes_placed_blocks_however_old(isolated_env, status):
    # placed: a working order on the terminal. awaiting_report: the operator
    # answered "yes, placed" and never reported the fill — either may be a
    # live position the fills journal cannot see, so NO time window (manager
    # decision 2026-09-29 17:24Z): a doubled prop position costs more than
    # one lost signal
    from src.prop.breakout_executor import STALE_PROMPT_GRACE

    _ticket("prop-manual-held", status=status)
    assert _reason(VALID_UNTIL + STALE_PROMPT_GRACE + timedelta(seconds=1)) == \
        f"outstanding_ticket:{status}: prop-manual-held"
    assert _reason(VALID_UNTIL + timedelta(days=30)) == \
        f"outstanding_ticket:{status}: prop-manual-held"


@pytest.mark.parametrize("valid_until", [None, "not-a-date"])
def test_a_prompt_with_no_readable_validity_still_blocks(isolated_env, valid_until):
    # fail-safe: a validity we cannot read is not known to have passed
    _ticket("prop-manual-novu", status="expiry_prompted", valid_until=valid_until)
    assert _reason(VALID_UNTIL + timedelta(days=30)) == \
        "outstanding_ticket:expiry_prompted: prop-manual-novu"


def test_an_emitted_ticket_still_leaves_by_its_own_validity(isolated_env):
    _ticket("prop-manual-emitted", status="emitted")
    assert _reason(VALID_UNTIL - timedelta(minutes=1)) == \
        "outstanding_ticket:emitted: prop-manual-emitted"
    assert _reason(VALID_UNTIL + timedelta(minutes=1)) is None


def test_a_stale_prompt_does_not_hide_a_live_ticket_behind_it(isolated_env):
    # the scan continues past a stale prompt: a newer live ticket still blocks
    _ticket("prop-manual-stale", status="expiry_prompted")
    live_vu = SIGNAL_0927 + timedelta(minutes=30)
    _ticket("prop-manual-live", status="emitted", valid_until=live_vu)
    assert _reason(SIGNAL_0927) == "outstanding_ticket:emitted: prop-manual-live"


def test_another_key_is_never_read(isolated_env):
    _ticket("prop-manual-eth", status="placed", symbol="ETHUSDT")
    assert _reason(SIGNAL_0927, symbol="SOLUSDT") is None
    assert _reason(SIGNAL_0927, symbol="ETHUSDT", direction="short") is None
