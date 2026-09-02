"""The venue per-order CEILING half of the qty-legalization seam.

`BL-20260810-ICTSCALP-AVAX-QTY-EXCEEDS-VENUE-MAX`.

MEASURED 2026-08-13 (diag #8987), which is what these numbers are: over
2026-07-31 -> 08-13 `ict_scalp_avax_5m` on bybit_1 had **18 orders bounced by
Bybit and 4 filled**. The filed row called it a stranded capability ("places
NOTHING; every order is EXCHANGE_REJECTED") and that was wrong — it is
SIZE-dependent. Bybit's own error text gives the cap exactly:

    order_qty:3452630000000 > max_qty:2200000000000 (ErrCode: 10001)
    3452630000000 / 34526.3 = 1e8  =>  max_qty = 2.2e12 / 1e8 = 22,000 AVAX

and that cap separates the outcomes 22 of 22 with NO overlap — largest fill
15,413.9 < 22,000 < smallest rejection 23,090.0.

`risk.py` had a per-account `min_qty` FLOOR and two ECONOMIC ceilings
(`max_qty_by_margin`, `max_qty_by_exposure`) but no per-instrument VENUE
maximum. The economic ceilings scale with equity and were satisfied here, so a
structurally illegal order passed every existing check.

THE SAFETY PROPERTY THESE TESTS EXIST TO PIN: the clamp is entered only when
`qty > max`, so it cannot alter an order the venue would have accepted. Every
currently-succeeding order is already below the cap. `test_currently_legal_*`
is therefore the most important test in this file — it is the negative control
for "does this change anything that works today?", and the answer must be no.
"""
from __future__ import annotations

import pytest

from src.units.accounts.qty_legalize import LegalizedQty, legalize_qty

BYBIT = {"exchange": "bybit"}
# The real AVAXUSDT shape on Bybit as of 2026-08-13.
AVAX_STEP, AVAX_MIN, AVAX_MAX = 0.1, 0.1, 22_000.0


def _rule(step=AVAX_STEP, vmin=AVAX_MIN, vmax=AVAX_MAX, source="live_lot_rule",
          max_state=None):
    """Inject a resolved lot rule, bypassing the venue round-trip.

    ⚠️ THESE TESTS BYPASS RESOLUTION, WHICH IS WHY THEY PASSED THROUGHOUT THE
    THIRD OCCURRENCE (2026-09-02). Every case here enters the clamp with a
    ceiling ALREADY resolved, so it exercises the CLAMP — which was correct all
    along — and never the resolution, where `venue_max` actually goes None.
    `tests/test_qty_legalize_venue_max_resolution.py` is the half that covers
    that, and it is where a regression of the live defect will show up.

    `max_state` (2026-09-02) defaults to the state the injected `vmax` implies:
    a value means the venue PUBLISHED a ceiling; None here means the caller is
    deliberately testing a venue with no ceiling (`absent`), NOT a failed read
    (`could_not_look`) — those are different states now and a test must say
    which one it means.
    """
    if max_state is None:
        max_state = "published" if vmax is not None else "absent"
    return lambda *a, **k: (step, vmin, vmax, max_state, source)


def _legalize(monkeypatch, qty, **rule_kw):
    monkeypatch.setattr(
        "src.units.accounts.qty_legalize._resolve_venue_lot_rule",
        _rule(**rule_kw),
    )
    return legalize_qty(qty, account_cfg=BYBIT, symbol="AVAXUSDT")


# --- the defect, at its measured values -----------------------------------

@pytest.mark.parametrize("qty", [23_090.0, 26_552.0, 30_434.3, 34_526.3])
def test_the_rejected_sizes_are_now_clamped_to_the_cap(monkeypatch, qty):
    """Every size Bybit actually bounced becomes a placeable order at the cap."""
    r = _legalize(monkeypatch, qty)
    assert r.ok is True, "a clamped order must be PLACED, not refused"
    assert r.qty == AVAX_MAX
    assert r.clamped is True
    assert r.venue_max == AVAX_MAX


@pytest.mark.parametrize("qty", [368.4, 3_217.2, 3_347.4, 15_413.9])
def test_currently_legal_sizes_are_untouched(monkeypatch, qty):
    """THE NEGATIVE CONTROL — the four sizes that really filled must be
    byte-for-byte unchanged, and must NOT be marked clamped.

    If this ever fails, the change has reached orders that already work, which
    is the only way this fix could lose money rather than recover it.
    """
    r = _legalize(monkeypatch, qty)
    assert r.ok is True
    assert r.qty == pytest.approx(qty)
    assert r.clamped is False


def test_absent_max_never_clamps(monkeypatch):
    """`None` = "the venue published no ceiling", NOT a ceiling of zero.

    Conflating them would refuse every order for that symbol — strictly worse
    than the bug being fixed.

    ⚠️ This test injects `absent` explicitly. Its previous docstring added
    "every non-Bybit venue and every static-map entry resolves this way", and
    that is FALSE as of 2026-09-02: those sources cannot SEE a ceiling, so they
    resolve `could_not_look`. Reading the second as the first is precisely the
    collapse that made the clamp a silent no-op three times.
    """
    r = _legalize(monkeypatch, 999_999.0, vmax=None)
    assert r.ok is True
    assert r.qty == pytest.approx(999_999.0)
    assert r.clamped is False
    assert r.venue_max is None


def test_qty_exactly_at_the_cap_is_not_a_clamp(monkeypatch):
    """Boundary: `==` is legal, so it must pass through unflagged.

    `clamped` is a real field rather than an inference from `qty == venue_max`
    precisely so this case cannot be mis-reported as a clamp in the log or the
    journal.
    """
    r = _legalize(monkeypatch, AVAX_MAX)
    assert r.ok is True and r.qty == AVAX_MAX and r.clamped is False


def test_clamped_value_is_floored_to_the_step(monkeypatch):
    """The cap need not be a multiple of the step; an unaligned qty is its own
    rejection, so clamping to a raw cap would swap one venue error for another."""
    r = _legalize(monkeypatch, 5_000.0, step=0.5, vmin=0.5, vmax=1_234.75)
    assert r.clamped is True
    assert r.qty == 1_234.5, "must floor to the step, never up past the cap"
    assert r.qty <= 1_234.75


def test_contradictory_rule_refuses_rather_than_guessing(monkeypatch):
    """max < min cannot be satisfied. Refuse — do not silently pick a bound."""
    r = _legalize(monkeypatch, 500.0, step=0.1, vmin=100.0, vmax=50.0)
    assert r.ok is False
    assert r.reason == "venue_max_below_min_qty"


def test_below_min_still_refuses_with_the_original_reason(monkeypatch):
    """The floor half must be untouched by the ceiling half."""
    r = _legalize(monkeypatch, 0.05, step=0.1, vmin=0.1, vmax=22_000.0)
    assert r.ok is False
    assert r.reason == "below_venue_min_qty"


def test_unknown_rule_is_still_passthrough(monkeypatch):
    """Rule unresolvable -> submit unmodified, the pre-seam contract."""
    monkeypatch.setattr(
        "src.units.accounts.qty_legalize._resolve_venue_lot_rule",
        lambda *a, **k: None,
    )
    r = legalize_qty(31_000.0, account_cfg=BYBIT, symbol="AVAXUSDT")
    assert r.ok is True and r.qty == pytest.approx(31_000.0)
    assert r.clamped is False and r.venue_max is None


def test_clamp_never_increases_a_quantity(monkeypatch):
    """Directional invariant over a wide sweep: legalization may only ever
    REDUCE. A ceiling that could round up would breach the sized risk cap."""
    for qty in (0.1, 1.0, 100.0, 21_999.9, 22_000.0, 22_000.1, 1e6):
        r = _legalize(monkeypatch, qty)
        if r.ok:
            assert r.qty <= qty + 1e-9, f"{qty} -> {r.qty} INCREASED"


def test_wire_string_matches_the_clamped_value(monkeypatch):
    """`qty_str` is what actually goes on the wire — if it kept the pre-clamp
    value the venue would bounce the order anyway and the fix would be inert."""
    r = _legalize(monkeypatch, 34_526.3)
    assert r.clamped is True
    assert float(r.qty_str) == r.qty == AVAX_MAX


def test_defaults_keep_the_dataclass_backwards_compatible():
    """Existing constructions omit the two new fields; they must not become
    required, or every pre-existing caller/test breaks."""
    r = LegalizedQty(qty=1.0, ok=True, reason="", venue_min=0.1, step=0.1,
                     source="unknown")
    assert r.venue_max is None and r.clamped is False
