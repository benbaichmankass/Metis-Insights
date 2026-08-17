"""`dir_offset_mismatch: 0` must not be readable as agreement over zero comparisons.

`dir_offset` exists for exactly one reason: to check the `_round_meta` offset
INDEPENDENTLY of the round report that states it. That check is only worth
anything if it actually ran.

Two layouts write arms in this tree, and they put the offset marker at different
levels::

    runtime_logs/m20_exit_head/<screen>/off8/rounds.jsonl   basename `off8`
    /tmp/m20_5m_wt_<ts>/off8/out/rounds.jsonl               basename `out`

The second is what the 2026-08-15 worktree-isolated screen writes. Against a
basename-only match every arm in that run resolves `dir_offset: None`, so
`dir_offset_mismatch` stays 0 -- not because the offsets agree but because
nothing was compared. That is the unasserted-denominator class (a clean-looking
negative over an empty population), and it would have landed silently on the
one run the field was added to protect.

Pinned here:
  1. both layouts resolve an offset;
  2. a real disagreement is still CAUGHT (the check is not merely permissive);
  3. the mismatch count always ships with its denominator.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "research" / "m20_consolidate_dispersion_arms.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_m20_consol", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["_m20_consol"] = m
    spec.loader.exec_module(m)
    return m


def _arm(base: Path, rel: str, meta_offset, rows=(("leg_a", 0.61),)):
    """Write one arm at `rel` with `_round_meta.fold_offset = meta_offset`."""
    d = base / rel
    d.mkdir(parents=True, exist_ok=True)
    report = {"_round_meta": {}}
    if meta_offset is not None:
        report["_round_meta"]["fold_offset"] = meta_offset
    (d / "round_report.json").write_text(json.dumps(report))
    (d / "rounds.jsonl").write_text(
        "".join(json.dumps({"leg": leg, "mean_auc": auc}) + "\n"
                for leg, auc in rows))
    return d


# ---------------------------------------------------------------- layouts

def test_the_flat_layout_still_resolves() -> None:
    m = _mod()
    assert m.dir_offset_for("/x/screen/off8") == 8


def test_the_nested_out_layout_resolves_too() -> None:
    """The layout the 2026-08-15 screen actually wrote."""
    m = _mod()
    assert m.dir_offset_for("/tmp/m20_5m_wt_20260815T161348Z/off8/out") == 8


def test_zero_offset_is_not_confused_with_absent() -> None:
    m = _mod()
    assert m.dir_offset_for("/tmp/run/off0/out") == 0      # a real 0
    assert m.dir_offset_for("/tmp/run/nothing/out") is None  # no claim


# ------------------------------------------------------- the denominator

def test_a_comparable_run_reports_a_NONZERO_denominator(tmp_path) -> None:
    m = _mod()
    _arm(tmp_path, "off0/out", 0)
    _arm(tmp_path, "off4/out", 4)
    rows, stats = m.consolidate(str(tmp_path))

    assert stats["arm_dirs"] == 2
    assert stats["dir_offset_comparable"] == 2, (
        "both arms state an offset in their path and in _round_meta, so both "
        f"must be compared; got {stats['dir_offset_comparable']}")
    assert stats["dir_offset_mismatch"] == 0
    assert {r["fold_offset"] for r in rows} == {0, 4}


def test_a_real_disagreement_is_CAUGHT(tmp_path) -> None:
    """Positive control: the check must be able to fail, or agreement is empty."""
    m = _mod()
    _arm(tmp_path, "off4/out", 8)          # path says 4, report says 8
    _, stats = m.consolidate(str(tmp_path))
    assert stats["dir_offset_comparable"] == 1
    assert stats["dir_offset_mismatch"] == 1, (
        "a path/report disagreement went unreported — the cross-check cannot "
        "distinguish agreement from indifference")


def test_an_UNCOMPARABLE_run_reports_a_ZERO_denominator(tmp_path) -> None:
    """The failure this whole module is about.

    No arm states an offset in its path, so `dir_offset_mismatch` is 0 — and
    the denominator is what says that 0 means nothing.
    """
    m = _mod()
    _arm(tmp_path, "armA/out", 0)
    _arm(tmp_path, "armB/out", 4)
    _, stats = m.consolidate(str(tmp_path))

    assert stats["arm_dirs"] == 2
    assert stats["dir_offset_mismatch"] == 0
    assert stats["dir_offset_comparable"] == 0, (
        "with no offset in any path the cross-check compared nothing; that "
        "must be visible in the stats rather than inferred from a bare 0")


def test_the_stats_always_carry_the_denominator(tmp_path) -> None:
    """Structural: the two keys travel together or the pair is unreadable."""
    m = _mod()
    _arm(tmp_path, "off0/out", 0)
    _, stats = m.consolidate(str(tmp_path))
    assert "dir_offset_mismatch" in stats and "dir_offset_comparable" in stats
    assert stats["dir_offset_comparable"] <= stats["arm_dirs"]
    assert stats["dir_offset_mismatch"] <= stats["dir_offset_comparable"], (
        "more mismatches than comparisons is arithmetically impossible and "
        "means the two are being counted over different populations")
