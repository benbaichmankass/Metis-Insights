"""Walk-forward folds must be cut so that TRADE FREQUENCY does not decide
whether a strategy can be graded at all.

WHY THIS EXISTS. `train_exit_head.py` cut one test fold per CALENDAR YEAR. A
daily-bar leg trades ~20x/year, so every year-fold landed 12-42 trades against a
50-trade floor and was skipped — while the pool held **371 trades across 19
years**. Measured 2026-08-13 on the trainer: BOTH 1d family pools returned ZERO
usable folds, and the conclusion drawn was "the 1d fleet cannot be graded."

That conclusion was wrong, and the error is worth naming precisely: the data was
always there, the slicing discarded it. Nothing about the population changed —
only the question we asked of it. `BL-20260813-E1-PER-YEAR-FOLD-UNSATISFIABLE-ON-DAILY-BARS`.

Slicing by trade count is NOT a weaker bar. What carries the statistics in a fold
is the number of TRADES in it, not the calendar span they cover.

THE LEAKAGE PROPERTY IS THE ONE THAT MATTERS. A fold scheme that grades more legs
while leaking labels is worse than one that grades none, so the cutoff invariant
is asserted directly rather than trusted.
"""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime, timezone

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "train_exit_head", REPO / "scripts" / "ml" / "train_exit_head.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _entry(bars):
    return bars[0]["bar_t"]


def _low_frequency_pool(n_trades=371, years=19):
    """The MEASURED shape of the donchian-1d pool: 371 trades over 19 years."""
    base = datetime(2008, 1, 1, tzinfo=timezone.utc).timestamp()
    span = years * 365 * 86400
    out = {}
    for i in range(n_trades):
        t = base + (i / n_trades) * span
        y = datetime.fromtimestamp(t, tz=timezone.utc).year
        out[f"t{i}"] = [{"bar_t": t, "year": y},
                        {"bar_t": t + 86400, "year": y}]
    return out


def test_calendar_folds_reproduce_the_bug():
    """The positive control for the DEFECT. If this ever stops failing the
    floor, the synthetic shape no longer matches what was measured and every
    other assertion here is about a different population."""
    blocks = _mod().fold_blocks(_low_frequency_pool(), "years", 50, _entry)
    usable = [t for _, _, t, _ in blocks if len(t) >= 50]
    assert blocks, "no folds at all — the shape is wrong, not the mode"
    assert usable == [], f"expected 0 usable year-folds, got {len(usable)}"


def test_trade_folds_grade_the_same_pool():
    """Same trades, same floor, same ordering — only the slicing changes."""
    blocks = _mod().fold_blocks(_low_frequency_pool(), "trades", 50, _entry)
    usable = [t for _, _, t, _ in blocks if len(t) >= 50]
    assert len(usable) >= 6, f"expected >=6 usable folds, got {len(usable)}"


def test_every_trade_fold_is_exactly_the_block_size():
    """`--min-fold-trades` is honoured BY CONSTRUCTION now, not by rejection —
    so a short block must never be emitted rather than emitted-and-skipped."""
    for block in (25, 50, 80):
        blocks = _mod().fold_blocks(_low_frequency_pool(), "trades", block, _entry)
        sizes = {len(t) for _, _, t, _ in blocks}
        assert sizes == {block}, f"block={block}: got sizes {sorted(sizes)}"


def test_no_test_trade_can_leak_into_its_own_training_set():
    """THE CORRECTNESS INVARIANT.

    The caller trains on trades whose LAST bar precedes `cutoff - EMBARGO`. So
    every test trade must ENTER at or after the cutoff — otherwise a trade could
    sit in both sides of its own fold and the fold would grade itself.
    """
    m = _mod()
    for _, _, test, cutoff in m.fold_blocks(_low_frequency_pool(), "trades", 50, _entry):
        earliest = min(_entry(b) for b in test.values())
        assert earliest >= cutoff, (earliest, cutoff)


def test_folds_are_ordered_and_disjoint():
    """Walk-forward, not shuffled: blocks advance in time and share no trade."""
    blocks = _mod().fold_blocks(_low_frequency_pool(), "trades", 50, _entry)
    cutoffs = [c for _, _, _, c in blocks]
    assert cutoffs == sorted(cutoffs), "folds are not chronological"
    seen = set()
    for _, _, test, _ in blocks:
        assert not (seen & set(test)), "a trade appears in two test folds"
        seen |= set(test)


def test_a_high_frequency_pool_is_unaffected_in_spirit():
    """Control: an intraday-frequency leg was never the broken case, and the new
    cut must not make it worse. 3000 trades over 3 years should yield many
    folds under both modes."""
    m = _mod()
    pool = _low_frequency_pool(n_trades=3000, years=3)
    for mode in ("years", "trades"):
        usable = [t for _, _, t, _ in m.fold_blocks(pool, mode, 50, _entry)
                  if len(t) >= 50]
        assert len(usable) >= 2, f"{mode}: {len(usable)} usable folds"


def test_the_check_can_fail():
    """A pool too small for one block must yield NO folds — otherwise the
    block-size assertion above passes vacuously on an empty list."""
    tiny = _low_frequency_pool(n_trades=30, years=2)
    assert _mod().fold_blocks(tiny, "trades", 50, _entry) == []


@pytest.mark.parametrize("mode", ["trades", "years"])
def test_both_modes_stay_available(mode):
    """`years` is kept to reproduce any pre-2026-08-13 result exactly. Removing
    it would make the old verdicts unreproducible, which is what makes the
    re-run comparable rather than merely newer."""
    assert _mod().fold_blocks(_low_frequency_pool(), mode, 50, _entry) is not None
