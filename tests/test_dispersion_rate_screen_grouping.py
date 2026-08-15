"""The arms of one run must group into ONE screen, whatever `--root` was.

`m20_dispersion_rate.group()` keys arms by `fold_offset` within a
`(screen, leg)` pair, and `rates()` EXCLUDES any pair holding a single arm as
"a leg that cannot move". So if `screen_of` splits one run's arms across
several screens, every pair becomes single-arm, every pair is excluded, and the
mover rate is computed over ZERO comparable pairs — printing a clean
"nothing moved" over an empty population.

`screen` is `relpath(arm_dir, root)`, so its shape depends on where the
consolidator's `--root` pointed. Two layouts exist::

    pull2h_20260815T095550Z/pull2h_off0   -> run is [0]
    off0/out                              -> [0] is the ARM

The second is what the 2026-08-15 worktree-isolated screen writes when `--root`
is the run dir (the driver passes `--out <arm>/out`). Under the old
`split("/")[0]` rule that run's four arms became four screens.

Pinned here:
  1. the committed 234-row corpus maps IDENTICALLY (this was a no-op there);
  2. a run whose arms are `off<N>/out` groups into one screen;
  3. sibling runs under a shared root do NOT merge;
  4. the exclusion path still works — a genuinely single-arm pair is still
     excluded, so fixing (2) did not simply stop excluding anything.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "research" / "m20_dispersion_rate.py"
CORPUS = REPO / "docs" / "research" / "m20-fold-dispersion-arms-consolidated.jsonl"


def _mod():
    spec = importlib.util.spec_from_file_location("_m20_rate", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["_m20_rate"] = m
    spec.loader.exec_module(m)
    return m


def _corpus() -> list[dict]:
    return [json.loads(line) for line in CORPUS.read_text().splitlines() if line.strip()]


def _row(screen, leg, off, verdict, unit="family_pooled"):
    return {"screen": screen, "leg": leg, "fold_offset": off,
            "verdict": verdict, "block_unit": unit}


# ------------------------------------------------- 1. no-op on the corpus

def test_the_committed_corpus_maps_identically() -> None:
    """A negative needs a denominator: assert the corpus is non-trivial first."""
    m, rows = _mod(), _corpus()
    assert len(rows) > 200, f"corpus unexpectedly small ({len(rows)}) — a no-op over it would prove little"

    changed = [r["screen"] for r in rows
               if str(r.get("screen") or "").split("/")[0] != m.screen_of(r)]
    assert not changed, (
        f"{len(changed)} committed row(s) regrouped, e.g. {changed[:3]} — the "
        "published 33.3%/26.7% headline would no longer be reproducible")


def test_the_published_headline_still_reproduces() -> None:
    """The rate itself, not just the mapping — arithmetic catches what reading misses."""
    m = _mod()
    r = m.rates(_corpus())
    assert r["screen_leg_pairs_with_multi_arms"] == 52
    assert r["screen_leg_pairs_excluded_single_arm"] == 14
    assert r["distinct_legs"] == 30
    text = m.render(r)
    assert "33.3%" in text and "26.7%" in text


# --------------------------------------- 2/3. the layouts that must work

def test_one_runs_arms_group_into_ONE_screen() -> None:
    """The 2026-08-15 layout: --root IS the run dir, so arms are `off<N>/out`."""
    m = _mod()
    got = {m.screen_of({"screen": f"off{n}/out"}) for n in (0, 4, 8, 12)}
    assert len(got) == 1, (
        f"one run's arms resolved to {len(got)} screens ({got}); every "
        "screen-leg pair would then hold a single arm and be excluded")


def test_sibling_runs_under_a_shared_root_do_NOT_merge() -> None:
    m = _mod()
    a = m.screen_of({"screen": "m20_5m_wt_A/off0/out"})
    b = m.screen_of({"screen": "m20_5m_wt_B/off0/out"})
    assert a != b, "two distinct runs collapsed into one screen"
    assert a == "m20_5m_wt_A" and b == "m20_5m_wt_B"


# ----------------------------------------- 4. the exclusion still bites

def test_a_genuinely_single_arm_pair_is_STILL_excluded() -> None:
    """Positive control.

    Grouping more rows together could 'fix' the rate by making the exclusion
    stop firing. It must still fire on a leg measured at exactly one offset.
    """
    m = _mod()
    rows = [
        _row("off0/out", "leg_moves", 0, "candidate"),
        _row("off4/out", "leg_moves", 4, "reject"),      # 2 arms -> comparable
        _row("off0/out", "leg_single", 0, "candidate"),  # 1 arm  -> excluded
    ]
    r = m.rates(rows)
    assert r["screen_leg_pairs_with_multi_arms"] == 1, r
    assert r["screen_leg_pairs_excluded_single_arm"] == 1, r


def test_the_two_arm_leg_is_seen_as_a_MOVER() -> None:
    """End-to-end: the regrouping must actually let a flip be detected."""
    m = _mod()
    rows = [_row("off0/out", "leg_moves", 0, "candidate"),
            _row("off4/out", "leg_moves", 4, "reject")]
    r = m.rates(rows)
    assert r["screen_leg_pairs_with_multi_arms"] == 1
    text = m.render(r)
    assert "1/1" in text, f"a verdict flip across two arms was not counted:\n{text}"
