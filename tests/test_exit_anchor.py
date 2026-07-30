"""Close-time exit anchoring — `src/runtime/exit_anchor.py`.

The Tier-2 remedy for the fabrication this workstream root-caused: pricing a
CONFIRMED CLOSE from `last_mark_price()`, the market at SWEEP time.

The contract these tests defend is the **three-way status**, because collapsing
any two of them reintroduces a defect:

* ``deferred``  — we did NOT look (budget spent / transient failure). Retry.
  Declaring here would record a gap we never searched for.
* ``no_anchor`` — we DID look and the venue has nothing. Declare unmeasured.
  Retrying forever here strands the row and re-opens the INV-2 pressure that
  caused the fabrication in the first place.
* ``anchored``  — a bar was found. ESTIMATED, never MEASURED.

No network: the fetcher is injected everywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.runtime import exit_anchor as EA


@pytest.fixture(autouse=True)
def _clean():
    EA.reset_caches()
    yield
    EA.reset_caches()


# A bar starting 2026-07-30T12:00:00Z, close 64100.5
_TS = int(datetime(2026, 7, 30, 12, 0, 30, tzinfo=timezone.utc).timestamp() * 1000)
_BAR_START = (_TS // 60_000) * 60_000


def _rows(close=64100.5):
    return [[str(_BAR_START), "1", "2", "3", str(close), "9", "9"]]


def _fetch_ok(symbol, start, end):
    return _rows()


def _fetch_empty(symbol, start, end):
    return []


def _fetch_fail(symbol, start, end):
    return None


# ------------------------------------------------------------------ anchored
def test_anchored_returns_the_bar_close():
    price, status = EA.bar_close_at("BTCUSDT", _TS, fetch=_fetch_ok)
    assert status == "anchored"
    assert price == 64100.5


def test_the_source_constant_is_the_estimated_one():
    """It must be the string `provenance` classifies as ESTIMATED — if these
    ever drift, an estimate would silently read as unverified (or worse)."""
    from src.runtime.provenance import ESTIMATED, classify
    assert classify(EA.ANCHOR_SOURCE) == ESTIMATED


def test_result_is_cached_per_symbol_and_minute():
    calls = []

    def _f(sym, a, b):
        calls.append(sym)
        return _rows()

    EA.bar_close_at("BTCUSDT", _TS, fetch=_f)
    EA.bar_close_at("BTCUSDT", _TS + 5_000, fetch=_f)   # same minute bucket
    assert len(calls) == 1


# ------------------------------------------------------------------ deferred
def test_budget_exhaustion_defers_and_does_not_declare():
    """THE distinction. A row we never looked at must be retried, not declared."""
    b = EA.AnchorBudget(1)
    p1, s1 = EA.bar_close_at("BTCUSDT", _TS, budget=b, fetch=_fetch_ok)
    p2, s2 = EA.bar_close_at("ETHUSDT", _TS, budget=b, fetch=_fetch_ok)
    assert s1 == "anchored"
    assert s2 == "deferred" and p2 is None


def test_transient_read_failure_defers_not_declares():
    """A failed HTTP read is NOT evidence the bar doesn't exist."""
    price, status = EA.bar_close_at("BTCUSDT", _TS, fetch=_fetch_fail)
    assert status == "deferred"
    assert price is None


def test_a_zero_budget_defers_everything():
    """`EXIT_ANCHOR_FETCHES_PER_TICK=0` must pause the network path safely —
    deferring, never fabricating."""
    b = EA.AnchorBudget(0)
    assert EA.bar_close_at("BTCUSDT", _TS, budget=b, fetch=_fetch_ok)[1] == "deferred"


# ----------------------------------------------------------------- no_anchor
def test_venue_with_no_bar_is_no_anchor():
    price, status = EA.bar_close_at("MES", _TS, fetch=_fetch_empty)
    assert status == "no_anchor"
    assert price is None


def test_unsupported_symbol_costs_exactly_one_request():
    """IBKR historical coverage is 0%, so every `MES` row would otherwise
    re-request per row per tick — on the live trader's monitor loop."""
    calls = []

    def _f(sym, a, b):
        calls.append(sym)
        return []

    for offset in range(0, 600_000, 60_000):   # ten DIFFERENT minute buckets
        EA.bar_close_at("MES", _TS + offset, fetch=_f)
    assert len(calls) == 1


def test_unsupported_negative_cache_does_not_consume_budget():
    b = EA.AnchorBudget(3)
    EA.bar_close_at("MES", _TS, budget=b, fetch=_fetch_empty)
    used_after_first = b.used
    EA.bar_close_at("MES", _TS + 120_000, budget=b, fetch=_fetch_empty)
    assert b.used == used_after_first == 1


def test_missing_symbol_or_close_time_is_no_anchor_not_deferred():
    """Nothing to anchor TO is a property of the ROW — a later tick won't help,
    so it must converge to a declaration rather than retry forever."""
    assert EA.bar_close_at("", _TS, fetch=_fetch_ok)[1] == "no_anchor"
    assert EA.bar_close_at("BTCUSDT", None, fetch=_fetch_ok)[1] == "no_anchor"
    assert EA.bar_close_at("BTCUSDT", "garbage", fetch=_fetch_ok)[1] == "no_anchor"


def test_nonpositive_close_is_no_anchor():
    assert EA.bar_close_at("BTCUSDT", _TS, fetch=lambda *a: _rows(0.0))[1] == "no_anchor"


def test_malformed_row_defers_rather_than_declaring():
    """An unparseable payload is a read problem, not an absence of data."""
    bad = [["notanumber"]]
    assert EA.bar_close_at("BTCUSDT", _TS, fetch=lambda *a: bad)[1] == "deferred"


# ------------------------------------------------------- closed_at parsing
@pytest.mark.parametrize("value,expected", [
    ("2026-07-30T12:00:30Z", _TS),
    ("2026-07-30 12:00:30", _TS),
    ("2026-07-30T12:00:30+00:00", _TS),
    (str(_TS), _TS),                      # raw epoch-ms STRING — the reconciler path
    (_TS, _TS),
])
def test_closed_at_parsing(value, expected):
    assert EA.closed_at_to_ms(value) == expected


def test_epoch_ms_string_is_not_misread_as_a_date():
    """The reconciler-filled close path writes closed_at as a raw epoch-ms
    string — the exact shape that silently dropped rows from /performance's
    window before `_closed_at.py` normalised it."""
    assert EA.closed_at_to_ms("1785412830000") == 1785412830000


@pytest.mark.parametrize("value", [None, "", "   ", "not-a-time", "2026-13-45"])
def test_unparseable_closed_at_is_none(value):
    assert EA.closed_at_to_ms(value) is None


def test_never_raises_on_hostile_input():
    for sym in (None, 12, object(), ""):
        for ts in (None, "x", object(), -1):
            price, status = EA.bar_close_at(sym, ts, fetch=_fetch_ok)
            assert status in ("anchored", "deferred", "no_anchor")
