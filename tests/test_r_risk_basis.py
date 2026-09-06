"""MI-144 — the R denominator is the trade's INITIAL risk, and an impossible
risk is REFUSED rather than ``abs()``-ed.

WHY THESE ASSERTIONS
--------------------
``trades.stop_loss`` holds the FINAL trailed stop (``order_monitor._apply_update``
mirrors every confirmed amend onto the row), so ``|entry - stop|`` collapses on a
trade trailed through breakeven and ``pnl / risk`` explodes. The legacy
``_clean_trades.r_multiple`` also ``abs()``-ed a stop stored on the WRONG SIDE of
entry into a small positive risk, so an impossible row produced a finite,
enormous R instead of being refused.

MEASURED, live journal pulled 2026-09-06 via ``/api/bot/db/table/{trades,
order_packages}`` (5518 + 4435 rows; the reproduction matched the live endpoint's
own totals exactly first, as a positive control):

* 30d real-money window, n=39 — published ``expectancyR +0.9818`` against
  ``totalPnl -3.6266`` / ``profitFactor 0.9507``. 12 rows (30.8%) graded
  ``contaminated`` carried 117.1% of that R.
* whole journal, n=1287 — 104 ``contaminated`` rows (8.1%) carried **96.6%** of
  ``totalR``; single-row maximum ``R = +3672.3``.
* ``paperPortfolio``, n=87 — published ``expectancyR +0.6253`` on a book that
  LOST $11,244.87 (``profitFactor 0.777``). On the declared-initial basis it
  reads ``-0.2736``.
"""
from __future__ import annotations

import pytest

from src.runtime.r_provenance import (
    R_BASES,
    R_BASIS_DECLARED,
    R_BASIS_NO_BASIS,
    R_BASIS_REFUSED_WRONG_SIDE,
    R_BASIS_STORED_STOP,
    empty_basis_counts,
    initial_risk_usd,
    r_multiple_provenanced,
)
from src.web.api._clean_trades import r_multiple


def _row(**kw):
    base = {
        "pnl": 100.0, "entry_price": 100.0, "stop_loss": 99.0,
        "qty": 1.0, "direction": "long",
    }
    base.update(kw)
    return base


def test_declared_initial_risk_wins_over_a_trailed_stop():
    """The signal's own ``risk_per_unit`` is preferred, because no trailing
    amend can reach ``order_packages.meta``. A stop trailed to 0.01 away from
    entry must NOT become the denominator."""
    row = _row(stop_loss=99.99, package_meta='{"risk_per_unit": 2.0}')
    r, basis = r_multiple_provenanced(row, 1.0)
    assert basis == R_BASIS_DECLARED
    assert r == pytest.approx(50.0)          # 100 / (2.0 * 1 * 1)
    # The legacy basis would have published 10000x that.
    assert r_multiple(100.0, 100.0, 99.99, 1.0, 1.0) == pytest.approx(10000.0)


def test_wrong_side_stop_is_REFUSED_not_absed():
    """A long whose stop sits ABOVE entry cannot be an initial stop. With no
    declared record to fall back to, the row is refused — it counts in NEITHER
    the R numerator nor its denominator."""
    row = _row(stop_loss=101.0)              # long, stop above entry
    r, basis = r_multiple_provenanced(row, 1.0)
    assert r is None
    assert basis == R_BASIS_REFUSED_WRONG_SIDE
    # The legacy helper produced a confident, finite, WRONG number here.
    assert r_multiple(100.0, 100.0, 101.0, 1.0, 1.0) == pytest.approx(100.0)


def test_wrong_side_stop_still_uses_a_declared_record_when_one_exists():
    """A refusal is the LAST resort, not the first. When the trade declared its
    initial risk we know the answer and must publish it."""
    row = _row(stop_loss=101.0, package_meta={"risk_per_unit": 4.0})
    r, basis = r_multiple_provenanced(row, 1.0)
    assert basis == R_BASIS_DECLARED
    assert r == pytest.approx(25.0)


def test_mirrored_bracket_reduce_leg_is_not_refused():
    """An ``intent_reduce`` row's whole bracket is inverted relative to its own
    ``direction`` — the side test's INPUT is unreliable there, which is not
    evidence of a trail. It keeps the stored basis rather than being refused."""
    row = _row(stop_loss=101.0, take_profit_1=99.0)   # BOTH legs wrong-side
    r, basis = r_multiple_provenanced(row, 1.0)
    assert basis == R_BASIS_STORED_STOP
    assert r == pytest.approx(100.0)


def test_stored_stop_branch_is_byte_for_byte_the_legacy_helper():
    """The one guarantee that keeps the two implementations from drifting: on
    the ``stored_stop`` basis this MUST equal ``_clean_trades.r_multiple``.

    Asserted rather than claimed in a docstring — a second copy of the same
    arithmetic is exactly how the pair would silently diverge.
    """
    cases = [
        # (pnl, entry, stop, qty, contract_value)
        (100.0, 100.0, 99.0, 1.0, 1.0),
        (-37.5, 2244.05, 2229.10, 3.0, 1.0),
        (1490.02, 62936.7, 62876.74, 0.25, 1.0),
        (-9.0, 1.2345, 1.2400, 1000.0, 1.0),      # short-shaped distance
        (12.0, 6.5, 6.1, 2.0, 100.0),             # multiplier-bearing contract
    ]
    for pnl, entry, stop, qty, cv in cases:
        direction = "long" if stop < entry else "short"
        r, basis = r_multiple_provenanced(
            {"pnl": pnl, "entry_price": entry, "stop_loss": stop,
             "qty": qty, "direction": direction}, cv)
        assert basis == R_BASIS_STORED_STOP
        assert r == pytest.approx(r_multiple(pnl, entry, stop, qty, cv))


def test_missing_inputs_grade_no_basis_and_never_fabricate_a_zero():
    for row in (_row(qty=None), _row(entry_price=None, package_meta=None),
                _row(stop_loss=100.0)):                 # zero distance
        r, basis = r_multiple_provenanced(row, 1.0)
        assert r is None
        assert basis == R_BASIS_NO_BASIS
    # A zero/absent contract value is also "no basis", never a divide-by-zero.
    r, basis = r_multiple_provenanced(_row(), 0.0)
    assert (r, basis) == (None, R_BASIS_NO_BASIS)


def test_a_missing_pnl_is_not_a_zero_R():
    r, basis = r_multiple_provenanced(_row(pnl=None), 1.0)
    assert r is None
    # The RISK was resolvable; only the numerator was absent. The basis still
    # reports what it resolved, so a consumer can tell the two apart.
    assert basis == R_BASIS_STORED_STOP


def test_every_basis_has_an_explicit_zero_and_the_partition_is_total():
    counts = empty_basis_counts()
    assert set(counts) == set(R_BASES)
    assert all(v == 0 for v in counts.values())
    # The four bases are exhaustive: every row lands in exactly one, so a
    # consumer can check its partition with arithmetic rather than trust it.
    rows = [_row(), _row(stop_loss=101.0), _row(qty=None),
            _row(package_meta='{"risk_per_unit": 1.5}')]
    for row in rows:
        counts[r_multiple_provenanced(row, 1.0)[1]] += 1
    assert sum(counts.values()) == len(rows)


def test_initial_risk_usd_scales_with_qty_and_contract_value():
    """R must put a micro crypto trade and a futures contract on ONE axis, so
    the declared per-unit risk is multiplied by size AND multiplier exactly as
    the stored-stop basis is."""
    risk, basis = initial_risk_usd(
        {"entry_price": 100.0, "stop_loss": 99.0, "qty": 3.0,
         "direction": "long", "package_meta": {"risk_per_unit": 2.0}}, 50.0)
    assert basis == R_BASIS_DECLARED
    assert risk == pytest.approx(2.0 * 3.0 * 50.0)
