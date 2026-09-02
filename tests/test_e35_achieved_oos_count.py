"""The e35 corpus must carry an ACHIEVED OOS sample size, not a run target.

WHY THIS EXISTS
---------------
R4's power gate needs to know whether a cell was judged on enough data. Until
2026-08-31 the e35 corpus carried no achieved count at all, so
`research_disposition.N_FIELD["e35"]` was `None` and every e35 unit was
UNGRADEABLE — 28 of them sat in exactly that state.

The tempting field, `split_target_oos`, is wrong for two reasons — and ⚠️ **one
of the two has since expired, so do not quote the original sentence.** As
written on 2026-08-31 it read: *"it is non-null on 377 of 8,321 rows (4.5%) with
exactly ONE distinct value, 50."* The constant half is now FALSE.

RE-MEASURED 2026-08-31, later the same day, over the whole corpus: 8,520 rows,
`split_target_oos` non-null on **566 (6.6%)** carrying **TWO** values —
`{50: 377, 60: 189}`. The 60s arrived with the re-sweep at a higher OOS target
(#10602, #10604). The pin below caught it, which is the pin working.

WHAT SURVIVES, AND IT IS THE DECIDING HALF: it is a run **TARGET**, not a
measurement. A target says what the sweep asked for, never what it achieved, so
it cannot answer *"was this cell judged on enough data?"* — which is the only
question the power gate asks. That is unaffected by how many distinct values it
takes.

WHAT ALSO SURVIVES: **coverage**. Keying the gate on it would grade 6.6% of the
corpus and call the other 93.4% unknown. (`base_oos_trades`, the right field,
is itself only non-null on 287 rows / 3.4% today — it is backfilling as sweeps
re-run, and a lower number here is NOT an argument for the wrong field.)

WHAT DIED: the "it is a constant, so it cannot discriminate" argument. It was
never the load-bearing one, and it is retired rather than repaired — leaving it
in place would let a future reader think the choice rests on something the
corpus no longer supports.

The producer emitted the real counts all along —
`e35_bracket_geometry_sweep.gate` writes `base_is_trades` / `base_oos_trades`,
both `run_cell(...)["total_trades"]` — and the extractor never read them.
Written-and-never-read is the `exit_price_source` shape this repo already pays
for, one layer down.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXTRACT = REPO / "scripts/research/e35_corpus_extract.py"


def _report(**gate_extra):
    """A minimal report.json in the shape the extractor's own selftest uses."""
    gate = {
        "cell": "sm1.5", "verdict": "is_oos_fail",
        "is": {"passed": True, "d_net_r": 1.0, "reason": "ok"},
        "oos": {"passed": False, "d_net_r": -2.0, "reason": "net_r_worse"},
        "split_meta": {"split_mode": "oos-trades", "split_target_oos": 50},
    }
    gate.update(gate_extra)
    return {
        "generated_at": "2026-08-31T04:00:00+00:00",
        "tp_cap_pct": 0.099,
        "fee_bps_roundtrip": 0.0,
        "legs": [{
            "leg": "demo_leg", "symbol": "GLD", "tf": "1h",
            "family": "pullback", "execution": "live",
            "base": {"net_total_r": 1.0, "max_drawdown_r": -1.0},
            "cells": [{"cell": "sm1.5", "axis": "stop", "stop_mult": 1.5,
                       "net_total_r": 2.0, "max_drawdown_r": -1.0,
                       "net_expectancy_r": 0.1, "d_net_r": 1.0, "d_max_dd": 0.0}],
            # `gate` is nested INSIDE the leg — the extractor builds
            # gate_by_cell from leg["gate"], not from a top-level key.
            "gate": [gate],
        }],
    }


def _extract(tmp_path, report) -> list[dict]:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "report.json").write_text(json.dumps(report))
    store = tmp_path / "corpus.jsonl"
    r = subprocess.run(
        [sys.executable, str(EXTRACT), str(run_dir), "--corpus", str(store)],
        cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return [json.loads(ln) for ln in store.read_text().splitlines() if ln.strip()]


def test_the_achieved_oos_count_reaches_the_corpus(tmp_path):
    """The positive control: when the producer emits it, the row carries it."""
    rows = _extract(tmp_path, _report(base_oos_trades=137, base_is_trades=402))
    assert rows, "no rows extracted"
    assert rows[0]["base_oos_trades"] == 137
    assert rows[0]["base_is_trades"] == 402


def test_an_absent_count_is_null_never_zero(tmp_path):
    """A zero would assert a MEASURED empty sample. Absent is not empty."""
    rows = _extract(tmp_path, _report())
    assert rows[0]["base_oos_trades"] is None
    assert rows[0]["base_is_trades"] is None


def test_the_power_gate_reads_the_achieved_field_not_the_target():
    """N_FIELD must not point at `split_target_oos`.

    It is a run setting with one distinct value across the corpus; keying the
    gate on it would compare every cell against a constant.
    """
    sys.path.insert(0, str(REPO))
    from scripts.research.research_disposition import N_FIELD
    assert N_FIELD["e35"] == "base_oos_trades", N_FIELD["e35"]
    assert N_FIELD["e35"] != "split_target_oos"


#: The `split_target_oos` values observed in the live corpus, as a tripwire.
#: It was `{50}` until 2026-08-31, when the re-sweep at a higher OOS target
#: (#10602, #10604) added 60 and the pin below fired — correctly. Widening it
#: is NOT the point and must never be the reflex: the point is that a new value
#: sends a reader back to the module docstring, which has since had to retire
#: one of its two arguments. Add a value here only after re-reading it.
OBSERVED_SPLIT_TARGETS = {50, 60}


def test_split_target_oos_is_still_the_wrong_field_for_the_power_gate():
    """The corpus-side tripwire behind `N_FIELD`'s choice.

    Renamed from `..._really_is_a_constant_in_the_live_corpus` on 2026-08-31.
    The old name asserted the argument that DIED — see the module docstring;
    a test named for a retired claim is how a reader learns the wrong lesson
    from a passing suite.

    Two assertions, deliberately, because they fail for different reasons and
    a reader must not confuse them:

    1. COVERAGE — the field is sparse, so keying the gate on it would grade a
       small slice and call the rest unknown. This is the argument that carries
       the choice now, so it is the one asserted numerically.
    2. THE VALUE TRIPWIRE — a value outside `OBSERVED_SPLIT_TARGETS` means a
       sweep changed the target again, and the docstring's reasoning needs
       re-reading rather than trusting. It does NOT by itself mean the field
       became usable.

    Neither replaces `test_the_power_gate_reads_the_achieved_field_not_the_target`
    above, which pins the decision itself and is unaffected by the corpus.
    """
    store = REPO / "docs/research/e35-bracket-corpus.jsonl"
    if not store.exists():
        return
    vals, rows, non_null = set(), 0, 0
    with store.open() as fh:
        for line in fh:
            if not line.strip():
                continue
            rows += 1
            v = json.loads(line).get("split_target_oos")
            if v is not None:
                non_null += 1
                vals.add(v)
    assert rows, "corpus is empty — this test would be vacuous"

    # 1. Coverage. Stated as a ceiling, not a band: the failure mode worth
    #    catching is the field quietly becoming near-universal, which would be
    #    the one development that could reopen the choice. A LOW number needs
    #    no alarm — it only reinforces it.
    assert non_null < rows * 0.5, (
        f"split_target_oos now covers {non_null}/{rows} "
        f"({100 * non_null / rows:.1f}%) of the corpus — it was 6.6% when the "
        "coverage argument was written. Re-read the module docstring before "
        "assuming N_FIELD's choice still holds."
    )

    # 2. The value tripwire.
    assert vals <= OBSERVED_SPLIT_TARGETS, (
        f"split_target_oos took an unrecorded value: {sorted(vals - OBSERVED_SPLIT_TARGETS)} "
        f"(observed set is {sorted(OBSERVED_SPLIT_TARGETS)}). A sweep changed the "
        "OOS target again. Re-read the module docstring, then add the value — "
        "in that order."
    )


# ─────────────────────────────────────────────────────────────────────────────
# THE ASSERTION THAT WAS MISSING — the target must keep DIVERGING from the
# achieved count. Everything above pins coverage and the observed value set;
# ⚠️ NOT ONE OF THEM WOULD FAIL IF `split_target_oos` STARTED TRACKING
# `base_oos_trades`, which is the single development that would actually
# reopen `N_FIELD`'s choice. The module docstring records 97.6% divergence as a
# MEASURED number and says in terms that it is not guarded. This guards it.
# ─────────────────────────────────────────────────────────────────────────────
def target_vs_achieved(rows) -> tuple[int, int]:
    """(rows where the two differ, rows carrying BOTH fields).

    The denominator is rows carrying BOTH, not all rows: a row with only one of
    them says nothing about whether the two track each other.
    """
    differ = both = 0
    for row in rows:
        t, a = row.get("split_target_oos"), row.get("base_oos_trades")
        if t is None or a is None:
            continue
        both += 1
        if t != a:
            differ += 1
    return differ, both


#: Below this share of divergence, `split_target_oos` is tracking the achieved
#: count closely enough that "it is a target, not a measurement" has stopped
#: being observably true, and `N_FIELD`'s justification needs re-reading rather
#: than trusting. 0.5 is deliberately far below the measured 97.6%: the tripwire
#: is for a REGIME CHANGE, not for ordinary drift.
MIN_DIVERGENCE = 0.5

#: ⚠️ A DENOMINATOR FLOOR, AND IT IS THE HALF THAT IS EASY TO OMIT. With no
#: floor, a corpus in which NO row carries both fields yields 0/0, and any
#: ratio test over it either divides by zero or is written defensively enough to
#: PASS — a permanently-green check over a corpus that stopped carrying the
#: evidence. That is the exact shape of a guard that cannot fail.
MIN_BOTH = 50


def assert_target_diverges_from_achieved(rows) -> tuple[int, int]:
    differ, both = target_vs_achieved(rows)
    if both < MIN_BOTH:
        raise AssertionError(
            f"only {both} row(s) carry BOTH split_target_oos and "
            f"base_oos_trades (floor {MIN_BOTH}). This check cannot say "
            "anything about whether the target tracks the achieved count, and "
            "must NOT be read as a pass — 0/0 is not 100% divergence.")
    if differ / both <= MIN_DIVERGENCE:
        raise AssertionError(
            f"split_target_oos now differs from base_oos_trades on only "
            f"{differ}/{both} ({100 * differ / both:.1f}%) of rows carrying "
            f"both — it was 97.6% (280/287) when N_FIELD's choice was last "
            "re-measured. The target appears to have started TRACKING the "
            "achieved count. Re-read research_disposition.N_FIELD's docstring "
            "and decide the field on the merits; do not lower this threshold.")
    return differ, both


def test_divergence_control_a_tracking_target_FAILS():
    """PLANTED CONTROL 1 (required by the criterion): target == achieved."""
    rows = [{"split_target_oos": 50, "base_oos_trades": 50} for _ in range(200)]
    with pytest.raises(AssertionError, match="started TRACKING"):
        assert_target_diverges_from_achieved(rows)


def test_divergence_control_no_row_carrying_both_FAILS():
    """PLANTED CONTROL 2 (required by the criterion): an empty denominator.

    0/0 must read as "this check could not look", never as a clean pass.
    """
    rows = [{"split_target_oos": 50}, {"base_oos_trades": 42}, {}]
    with pytest.raises(AssertionError, match="must NOT be read as a pass"):
        assert_target_diverges_from_achieved(rows)


def test_divergence_control_a_diverging_corpus_PASSES():
    """NEGATIVE CONTROL: the checker does not simply reject everything."""
    rows = [{"split_target_oos": 50, "base_oos_trades": 30 + i} for i in range(200)]
    differ, both = assert_target_diverges_from_achieved(rows)
    assert both == 200 and differ >= 199


def test_split_target_oos_still_diverges_from_the_achieved_count():
    """The live corpus, through the same checker the controls exercised."""
    store = REPO / "docs/research/e35-bracket-corpus.jsonl"
    if not store.exists():
        return
    rows = [json.loads(ln) for ln in store.read_text().splitlines() if ln.strip()]
    differ, both = assert_target_diverges_from_achieved(rows)
    # Recorded, not pinned to an exact value: a re-sweep legitimately moves both
    # numbers. MEASURED 2026-09-02 at 943a7192: 280/287 = 97.6%.
    assert differ / both > MIN_DIVERGENCE, (differ, both)
