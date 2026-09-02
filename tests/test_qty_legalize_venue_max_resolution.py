"""The RESOLUTION half of the venue per-order ceiling — the third occurrence.

`BL-20260902-AVAX-VENUE-MAX-CLAMP-INERT-WHEN-THE-LIVE-LOOKUP-MISSES`
(recurrence of BL-20260810-ICTSCALP-AVAX-QTY-EXCEEDS-VENUE-MAX, filed
`resolved`, and BL-20260821-AVAX-SCALP-SIZES-ABOVE-THE-VENUE-MAXIMUM, open;
mechanism named in BL-20260814-VENUE-MAX-NONE-CANNOT-SAY-WE-COULD-NOT-LOOK).

WHY THE EXISTING SUITE DID NOT CATCH THIS. `tests/test_qty_legalize_venue_max.py`
monkeypatches `_resolve_venue_lot_rule` to return a rule that ALREADY carries
`vmax=22_000`, so every one of its cases enters the clamp branch. It tests the
CLAMP, which was always correct. Nothing tested the RESOLUTION, which is where
`venue_max` becomes `None` — and a `None` ceiling makes the clamp a no-op.

MEASURED, live on bybit_1 at 2026-09-02T09:06:11Z — `ict_scalp_avax_5m`,
8 signals, 0 orders placed, every one exchange-rejected:

    order_qty:2299510000000 > max_qty:2200000000000 (ErrCode: 10001)
    2299510000000 / 1e8 = 22995.1        2200000000000 / 1e8 = 22000

and the wire payload carried `"qty":"22995.1"` with `tpSize`/`slSize` at the
same value — exactly what an UNCLAMPED `legalize_qty` emits, since
`_submit_order` sets all three from `_legal.qty_str`.

THE COLLAPSED STATE. `venue_max=None` is produced by three structurally
different conditions that `legalize_qty` cannot tell apart, and it treats all
three as "no ceiling exists — place it":

  1. the venue published no maximum   (we looked; there is no ceiling)
  2. the live lookup failed / was empty, and the offline `InstrumentProfile`
     answered instead — the profile has no `max_qty` field at all
  3. the static fallback map answered — it is documented as asserting no
     ceiling, i.e. it cannot speak to one

Only (1) justifies placing unclamped. (2) and (3) are *we could not look*, and
AVAXUSDT is not in the static map, so its production path is (2).
"""
from __future__ import annotations

import pytest

from src.units.accounts import qty_legalize
from src.units.accounts.qty_legalize import legalize_qty

# bybit_1 as configured: `market_type: linear` (config/accounts.yaml).
BYBIT_1 = {"exchange": "bybit", "market_type": "linear", "account_id": "bybit_1"}

REJECTED_QTY = 22_995.1   # what went on the wire
AVAX_VENUE_MAX = 22_000.0  # what Bybit's own error names as the cap


class _DeadInstrumentsClient:
    """A client whose instruments-info read fails — the *we could not look* case.

    Deliberately raises rather than returning an empty payload: both reach the
    same `None`, and the point of this suite is that they must not reach the
    same DECISION.
    """

    def get_instruments_info(self, **_kw):
        raise RuntimeError("instruments-info unreachable (simulated outage)")


class _NoCeilingClient:
    """A client whose read SUCCEEDS and whose lotSizeFilter carries no max."""

    def get_instruments_info(self, **_kw):
        return {"result": {"list": [
            {"lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1"}},
        ]}}


@pytest.fixture(autouse=True)
def _clear_caches():
    """The lot cache and profile cache are process-global; isolate each test."""
    from src.units.accounts import precision
    precision._LOT_CACHE.clear()
    qty_legalize._reset_profile_cache()
    yield
    precision._LOT_CACHE.clear()
    qty_legalize._reset_profile_cache()


# --- the live defect, reproduced through the REAL resolution path ----------

def test_the_live_rejection_is_not_placed_unclamped():
    """THE REGRESSION. Live lookup misses -> the offline profile answers ->
    the order must still be clamped to the venue cap, not sent at 22995.1.

    This is the exact order Bybit bounced 8 times on 2026-09-02.
    """
    r = legalize_qty(
        REJECTED_QTY, account_cfg=BYBIT_1, symbol="AVAXUSDT",
        client=_DeadInstrumentsClient(), prefer_live=True,
    )
    assert r.ok is True, "a clamped order must be PLACED, not refused"
    assert r.qty == AVAX_VENUE_MAX, (
        f"qty {r.qty} would be bounced by Bybit (max {AVAX_VENUE_MAX}); "
        "the clamp no-opped because venue_max resolved None"
    )
    assert r.clamped is True
    # The wire string is what actually reaches the exchange (it also feeds
    # tpSize/slSize), so a clamp that did not reach it would be inert.
    assert float(r.qty_str) == AVAX_VENUE_MAX


def test_we_could_not_look_is_not_reported_as_no_ceiling():
    """The collapsed state itself: a failed read must be distinguishable from
    a venue that genuinely published no maximum."""
    could_not_look = legalize_qty(
        100.0, account_cfg=BYBIT_1, symbol="AVAXUSDT",
        client=_DeadInstrumentsClient(), prefer_live=True,
    )
    absent_ceiling = legalize_qty(
        100.0, account_cfg=BYBIT_1, symbol="AVAXUSDT",
        client=_NoCeilingClient(), prefer_live=True,
    )
    assert could_not_look.venue_max_state != absent_ceiling.venue_max_state, (
        "'we could not look' and 'the venue published no ceiling' collapsed "
        "into one value — the defect this contract exists to prevent"
    )
    assert absent_ceiling.venue_max_state == "absent"


def test_a_published_ceiling_is_stated_as_published():
    r = legalize_qty(
        100.0, account_cfg=BYBIT_1, symbol="AVAXUSDT",
        client=None, prefer_live=True,
    )
    # No client -> live miss -> offline profile. Once the profile carries the
    # ceiling this is a real, published answer from an offline source.
    assert r.venue_max_state == "published"
    assert r.venue_max == AVAX_VENUE_MAX


def test_absent_ceiling_still_places_unmodified():
    """The negative control. A venue with no ceiling must be untouched — this
    fix must not start clamping orders that are legal today."""
    r = legalize_qty(
        REJECTED_QTY, account_cfg=BYBIT_1, symbol="AVAXUSDT",
        client=_NoCeilingClient(), prefer_live=True,
    )
    assert r.ok is True and r.clamped is False
    assert r.qty == pytest.approx(REJECTED_QTY)
    assert r.venue_max_state == "absent"


def test_unknown_symbol_is_could_not_look_not_absent():
    """A symbol with neither a profile nor a live rule: we could not look.
    It still passes through (no new refusals), but it must SAY so."""
    r = legalize_qty(
        5.0, account_cfg=BYBIT_1, symbol="NOSUCHUSDT",
        client=_DeadInstrumentsClient(), prefer_live=True,
    )
    assert r.ok is True and r.qty == pytest.approx(5.0)
    assert r.venue_max_state == "could_not_look"


def test_clamp_still_never_increases_a_quantity():
    """Directional invariant, re-asserted over the resolution path."""
    for qty in (0.1, 1.0, 21_999.9, 22_000.0, 22_000.1, REJECTED_QTY, 1e6):
        r = legalize_qty(
            qty, account_cfg=BYBIT_1, symbol="AVAXUSDT",
            client=_DeadInstrumentsClient(), prefer_live=True,
        )
        if r.ok:
            assert r.qty <= qty + 1e-9, f"{qty} -> {r.qty} INCREASED"
