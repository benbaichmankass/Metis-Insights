"""The IB session gate's parser.

These pin the properties the close path depends on. The one that matters most is
not "does it parse" — it is that ``unknown`` never degrades into ``closed``,
because ``closed`` refuses to flatten a live position and the whole gate is only
safe if *we could not look* is a distinct, fail-permissive answer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import ib_trading_hours as th

# A realistic COMEX gold week as IBKR reports it: Saturday closed, then the
# electronic session opening 18:00 ET and running to 17:00 ET the next day.
COMEX_TRADING = (
    "20260815:CLOSED;"                # Saturday
    "20260816:1800-20260817:1700;"    # Sunday 18:00 ET -> Monday 17:00 ET
    "20260817:1800-20260818:1700;"    # Monday 18:00 -> Tuesday 17:00
    "20260818:1800-20260819:1700"     # Tuesday 18:00 -> Wednesday 17:00
)
# The RTH/pit window inside it — what outsideRth=False actually keys on.
COMEX_LIQUID = (
    "20260815:CLOSED;"
    "20260816:CLOSED;"
    "20260817:0820-20260817:1330;"
    "20260818:0820-20260818:1330"
)
ET = "US/Eastern"


def _et(y, mo, d, h, mi=0):
    """A UTC instant for the given US/Eastern wall clock (August => UTC-4).

    Via timedelta, not `h + 4`: a wall clock of 20:00 or later would overflow the
    hour field and raise, which is how the first draft of these tests "failed".
    """
    midnight = datetime(y, mo, d, tzinfo=timezone.utc)
    return midnight + timedelta(hours=h + 4, minutes=mi)


# --- the property the gate's safety rests on --------------------------------

def test_instant_outside_the_covered_span_is_unknown_not_closed():
    """THE regression this module exists to avoid.

    IBKR returns roughly a week. An instant outside it matches no range — which
    is *identical* to a real closure unless coverage is checked separately. A
    two-state parser reports `closed`, the close path defers, and a live
    position is stranded on a fully open venue every time the cached string
    ages out.
    """
    state, reason = th.session_state(COMEX_TRADING, ET, now=_et(2026, 9, 1, 12))
    assert state == th.UNKNOWN, reason
    assert "OUTSIDE the covered span" in reason

    # ...and the same clock inside the span, outside every session, IS closed.
    inside, _ = th.session_state(COMEX_TRADING, ET, now=_et(2026, 8, 15, 12))
    assert inside == th.CLOSED


def test_unresolvable_timezone_is_unknown_never_a_verdict():
    for bad in (None, "", "Middle/Earth"):
        state, reason = th.session_state(COMEX_TRADING, bad, now=_et(2026, 8, 18, 12))
        assert state == th.UNKNOWN, (bad, reason)


def test_unparseable_hours_is_unknown_never_closed():
    for bad in (None, "", "garbage", "notadate:0800-0900"):
        state, _ = th.session_state(bad, ET, now=_et(2026, 8, 18, 12))
        assert state == th.UNKNOWN, bad


# --- the timezone resolution the futures venues actually need ---------------

def test_legacy_us_timezone_ids_resolve():
    """COMEX reports `US/Eastern` and CME `US/Central` — both are tzdata LEGACY
    links absent from slim installs (measured: zoneinfo raises for them in this
    repo's sandbox while `America/New_York` resolves). If these returned None
    the gate would be UNKNOWN for every futures contract we trade, which looks
    exactly like a working fail-permissive gate."""
    for tz_id in ("US/Eastern", "US/Central", "America/New_York", "GMT", "JST"):
        assert th.resolve_timezone(tz_id) is not None, tz_id


def test_timezone_is_never_silently_assumed_utc():
    """A US/Central string read as UTC shifts every boundary five or six hours.
    Abstaining is correct; guessing produces a confident wrong verdict."""
    assert th.resolve_timezone("Definitely/NotAZone") is None


# --- the open/closed boundary -----------------------------------------------

@pytest.mark.parametrize("hour,expected", [
    (17, th.CLOSED),   # 17:00 ET Monday — the daily break
    (18, th.OPEN),     # 18:00 ET — electronic reopen, inclusive
    (23, th.OPEN),
])
def test_electronic_session_boundaries(hour, expected):
    state, reason = th.session_state(COMEX_TRADING, ET, now=_et(2026, 8, 17, hour))
    assert state == expected, reason


def test_overnight_range_spans_midnight():
    """The 18:00 Monday -> 17:00 Tuesday session must read OPEN at 03:00 Tuesday;
    a same-day-only parser reports closed for the whole overnight, which is most
    of a futures week."""
    state, _ = th.session_state(COMEX_TRADING, ET, now=_et(2026, 8, 18, 3))
    assert state == th.OPEN


def test_explicit_closed_day_is_closed_not_unknown():
    """A day IBKR names CLOSED is covered data, not missing data."""
    state, reason = th.session_state(COMEX_TRADING, ET, now=_et(2026, 8, 15, 9))
    assert state == th.CLOSED, reason


# --- tradingHours vs liquidHours are different questions --------------------

def test_trading_and_liquid_hours_disagree_at_03_00_and_both_are_right():
    """03:00 ET Tuesday: the electronic session is OPEN but RTH is not. An order
    with outsideRth=False is HELD at that instant. Gating on one field while
    transmitting the other is the semantic substitution the module warns about,
    so the parser must be able to answer either question distinctly."""
    at = _et(2026, 8, 18, 3)
    assert th.session_state(COMEX_TRADING, ET, now=at)[0] == th.OPEN
    assert th.session_state(COMEX_LIQUID, ET, now=at)[0] == th.CLOSED


def test_details_helper_defaults_to_trading_hours():
    class _D:
        timeZoneId = ET
        tradingHours = COMEX_TRADING
        liquidHours = COMEX_LIQUID

    at = _et(2026, 8, 18, 3)
    state, reason = th.session_state_from_details([_D()], now=at)
    assert state == th.OPEN and "tradingHours" in reason
    state, reason = th.session_state_from_details([_D()], now=at, prefer_liquid=True)
    assert state == th.CLOSED and "liquidHours" in reason


def test_details_helper_degrades_to_unknown_on_junk():
    class _Empty:
        pass

    assert th.session_state_from_details(None)[0] == th.UNKNOWN
    assert th.session_state_from_details([])[0] == th.UNKNOWN
    assert th.session_state_from_details([_Empty()])[0] == th.UNKNOWN


# --- format tolerance -------------------------------------------------------

def test_legacy_format_is_parsed():
    """Which format arrives depends on the gateway's API version, which we do
    not control, so both must work."""
    legacy = "20260817:0700-1830,1900-2330;20260818:CLOSED"
    assert th.session_state(legacy, ET, now=_et(2026, 8, 17, 8))[0] == th.OPEN
    assert th.session_state(legacy, ET, now=_et(2026, 8, 17, 18, 45))[0] == th.CLOSED
    assert th.session_state(legacy, ET, now=_et(2026, 8, 17, 20))[0] == th.OPEN


def test_legacy_range_crossing_midnight_rolls_forward():
    assert th.session_state(
        "20260817:1800-1700", ET, now=_et(2026, 8, 18, 3)
    )[0] == th.OPEN


def test_one_malformed_day_does_not_blind_the_rest_of_the_week():
    mangled = "20260817:18OO-20260818:1700;20260818:1800-20260819:1700"
    ranges, covered = th.parse_hours(mangled)
    assert len(ranges) == 1 and len(covered) == 2
    assert th.session_state(mangled, ET, now=_et(2026, 8, 18, 20))[0] == th.OPEN


# ---------------------------------------------------------------------------
# WHICH library resolved the timezone. The gate is fail-permissive on `unknown`,
# so `state: "open"` proves a tz resolved but not THROUGH WHAT — and that
# distinction is the whole reason the pytz fallback exists
# (BL-20260817-VENUE-SESSION-HAS-NO-READ-SURFACE).
# ---------------------------------------------------------------------------


def test_tz_source_names_which_library_answered():
    tz, source, name = th.resolve_timezone_with_source("America/New_York")
    assert tz is not None
    assert source in th.TZ_SOURCES and source != th.TZ_UNRESOLVED
    assert name == "America/New_York"


def test_tz_source_reports_the_alias_that_actually_worked():
    """`US/Eastern` served as `America/New_York` must be VISIBLE, not assumed —
    a reader checking the raw id resolved would draw the wrong conclusion about
    the host's tzdata."""
    tz, source, name = th.resolve_timezone_with_source("US/Eastern")
    assert tz is not None
    assert source != th.TZ_UNRESOLVED
    assert name in ("US/Eastern", "America/New_York")


def test_unresolvable_tz_reports_unresolved_not_a_silent_none():
    tz, source, name = th.resolve_timezone_with_source("Middle/Earth")
    assert tz is None and source == th.TZ_UNRESOLVED and name is None


def test_resolve_timezone_delegates_so_there_is_one_resolution_order():
    """Two resolution paths would be free to drift; the wrapper must agree with
    the detailed function on every input."""
    for tz_id in ("US/Eastern", "US/Central", "America/New_York", "GMT",
                  "Middle/Earth", "", None):
        assert (th.resolve_timezone(tz_id) is None) == (
            th.resolve_timezone_with_source(tz_id)[0] is None
        ), tz_id
