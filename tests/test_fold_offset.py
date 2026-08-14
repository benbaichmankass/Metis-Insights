"""`--fold-offset` must shift the boundary, keep the block size, and REFUSE quietly-wrong uses.

This flag exists to measure how much an E1 verdict depends on where the fold
boundaries happen to fall. Six legs re-measured one day apart moved -0.110 to
+0.042 in `mean_auc` against a gate bar of 0.55, and one was graded `candidate`
on a 0.0025 margin the same night
(`BL-20260814-EXIT-HEAD-AUC-MOVES-MORE-THAN-ITS-OWN-GATE-MARGIN-ACROSS-A-ONE-DAY-RE-MEASUREMENT`).

The property under test is NOT "offset changes the folds" — that is trivially
true and would also be true of the wrong implementation. It is that the block
SIZE is untouched, so the spread the flag measures is boundary sensitivity and
not "AUC is noisier on smaller folds". Sweeping `--min-fold-trades` would
conflate exactly those two, which is why that flag's own comment forbids it.

The two refusals matter as much as the shift. A non-zero offset under
`--fold-mode=years` has nothing to shift; ignoring it would let a dispersion run
report five offsets measured when all five were the same partition — the failure
this flag exists to detect, inverted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ml"))

from train_exit_head import fold_blocks  # noqa: E402

BLOCK = 10


def _trades(n: int) -> dict:
    """`n` trades, one per hour, in a shape `fold_blocks` accepts."""
    return {f"t{i}": [{"ts": 1_700_000_000 + i * 3600, "year": 2026}]
            for i in range(n)}


def _t_entry(bars):
    return bars[0]["ts"]


def _sizes(blocks):
    return [len(test) for _label, _year, test, _t0 in blocks]


def test_block_size_is_unchanged_by_the_offset() -> None:
    """The whole point: boundary moves, block size does not.

    If an implementation shortened blocks instead of shifting them, the spread
    this flag measures would be contaminated by fold size — the exact confound
    that rules out the `--min-fold-trades` sweep.
    """
    tr = _trades(100)
    for k in (0, 1, 5, BLOCK - 1):
        sizes = _sizes(fold_blocks(tr, "trades", BLOCK, _t_entry, offset=k))
        assert set(sizes) == {BLOCK}, f"offset={k} produced fold sizes {sizes}"


def test_the_partition_actually_moves() -> None:
    """A no-op would pass the size test above while measuring nothing."""
    base = fold_blocks(_trades(100), "trades", BLOCK, _t_entry, offset=0)
    shifted = fold_blocks(_trades(100), "trades", BLOCK, _t_entry, offset=3)
    assert [sorted(t) for _l, _y, t, _c in base] != [sorted(t) for _l, _y, t, _c in shifted]


def test_offset_zero_is_byte_for_byte_the_old_behaviour() -> None:
    """The default must not perturb any recorded verdict."""
    tr = _trades(100)
    assert (fold_blocks(tr, "trades", BLOCK, _t_entry)
            == fold_blocks(tr, "trades", BLOCK, _t_entry, offset=0))


def test_years_mode_REFUSES_a_nonzero_offset_rather_than_ignoring_it() -> None:
    """Silently ignoring it is worse than failing: a dispersion run would then
    report N distinct offsets that were all the same partition, and the null
    result would read as 'boundaries do not matter'."""
    with pytest.raises(ValueError, match="fold-mode"):
        fold_blocks(_trades(100), "years", BLOCK, _t_entry, offset=3)
    # ...but zero stays legal, or every years-mode call would break.
    fold_blocks(_trades(100), "years", BLOCK, _t_entry, offset=0)


@pytest.mark.parametrize("bad", [-1, BLOCK, BLOCK + 1, 999])
def test_out_of_range_offsets_are_refused(bad: int) -> None:
    """`offset >= block_n` is not a new partition — it repeats one while
    discarding a whole block, which would silently shrink the population."""
    with pytest.raises(ValueError, match="out of range"):
        fold_blocks(_trades(100), "trades", BLOCK, _t_entry, offset=bad)


def test_the_skipped_head_is_reported_not_silently_dropped(capsys) -> None:
    """A population that shrinks without saying so is the standing failure mode
    here — the same reason the trailing partial block is announced."""
    fold_blocks(_trades(100), "trades", BLOCK, _t_entry, offset=4)
    out = capsys.readouterr().out
    assert "--fold-offset 4" in out and "96 of 100" in out, out


def test_offsets_are_exhaustive_and_distinct_over_the_legal_range() -> None:
    """`0..block_n-1` should give `block_n` genuinely different partitions —
    the denominator for 'how many independent boundary draws are available'."""
    tr = _trades(100)
    seen = {tuple(tuple(sorted(t)) for _l, _y, t, _c in
                  fold_blocks(tr, "trades", BLOCK, _t_entry, offset=k))
            for k in range(BLOCK)}
    assert len(seen) == BLOCK, f"only {len(seen)} distinct partitions of {BLOCK}"
