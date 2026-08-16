"""An exit-head round must leave evidence the repo can keep.

`--out` is a required, ephemeral trainer directory, so until 2026-08-14 a round
left NOTHING behind: every verdict reached the repo only as PROSE hand-copied
into a matrix `ref`. That is why 141 of 376 coverage cells rest on refs no guard
can check, and it is what makes operator decision (d) — "establish the base rate
from the corpus" — not executable for this lever.

The round now writes `rounds.jsonl` in the canonical shape of
`docs/research/m20-exit-head-rounds.jsonl`, so promoting a round's evidence is a
`cat` and an append rather than transcription.

TWO THINGS PINNED HERE, both of which were wrong at first:

  1. The FIELD NAMES come from `per_leg_summary`'s actual output
     (`oos_trades`, `mean_auc`, `beats_actual_folds`, `beats_hard_folds`), NOT
     from the names the matrix refs print. A first probe guessed `n_oos` /
     `beats_actual` and got `None` back for every leg — the data was there under
     other keys. Reading the producer is what settled it, and this test keeps
     the two from drifting apart.
  2. The GEOMETRY is written by the same derivation that stamps `_round_meta`.
     On 2026-08-14 a scalp round had to be hand-corrected from `live_parity` to
     `live_parity_uncapped` on the way into the evidence file. A label a human
     has to remember to fix is one they will eventually not fix.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "scripts" / "research" / "m20_exit_head_round.py"
TRAINER = REPO / "scripts" / "ml" / "train_exit_head.py"
EVIDENCE = REPO / "docs" / "research" / "m20-exit-head-rounds.jsonl"


def test_the_round_writes_an_evidence_file() -> None:
    src = DRIVER.read_text()
    assert "rounds.jsonl" in src, (
        "the round no longer emits evidence rows; its verdicts would again "
        "reach the repo only as hand-copied prose"
    )
    assert "evidence rows ->" in src, "the emit is silent; say where it went"


def test_it_reads_the_producers_real_field_names() -> None:
    """The names in `per_leg_summary`, not the ones the refs print."""
    driver = DRIVER.read_text()
    trainer = TRAINER.read_text()
    for produced in ("oos_trades", "mean_auc", "beats_actual_folds",
                     "beats_hard_folds", "usable_folds"):
        assert f'"{produced}"' in trainer, (
            f"per_leg_summary no longer produces {produced}; the emitter's "
            f"accessor is now guessing"
        )
        assert produced in driver, (
            f"the emitter stopped reading {produced} — it will write None"
        )


def test_the_emitted_geometry_is_the_derived_one_not_the_flag() -> None:
    # Anchor on the WRITE SITE, not on the first mention of the filename.
    # `src.index("rounds.jsonl")` used to land here because the string appeared
    # nowhere else; on 2026-08-15 a comment above the training loop mentioned
    # the file by name, the index jumped ~250 lines earlier, and this test
    # failed on a driver whose geometry stamping was untouched. A locator that
    # prose can move is a locator that reports the wrong thing — the failure
    # named the geometry stamp while the actual change was a comment.
    #
    # Bound it by the ROW LITERAL, not by a fixed character count backwards.
    # The old `src[i - 3000:i]` window is a proxy for "somewhere above the
    # write", and a 1,000-character comment added between the two pushed the
    # stamp out of the window while the code was unchanged — the same false
    # failure a second time, for a second reason. `rows.append({` … write-site
    # IS the region the assertion is about: the dict that becomes an evidence
    # row.
    src = DRIVER.read_text()
    write_site = '(out / "rounds.jsonl").write_text'
    assert write_site in src, (
        "the evidence file is no longer written by a literal "
        f"`{write_site}` call — re-derive this locator against the driver "
        "rather than loosening it back to a bare filename search")
    assert src.count("rows.append({") == 1, (
        "more than one row-append site; this locator assumed exactly one and "
        "would now check an arbitrary one of them")
    block = src[src.index("rows.append({"):src.index(write_site)]
    assert '"tp_geometry": geometry' in block, (
        "the evidence rows stamp something other than the derived geometry; "
        "a scalp round would write live_parity for a cap it never received"
    )


def test_the_row_records_WHICH_FOLD_PARTITION_produced_it() -> None:
    """The one axis the dispersion study varies must be on the row.

    `BL-20260815-EVIDENCE-ROWS-DO-NOT-RECORD-FOLD-OFFSET`. The rows carried
    `total_sort` and `block_unit` — the axes the study holds FIXED — and not
    `fold_offset`, the axis it varies. `_round_meta` had it, so the round knew;
    rows are what get consolidated, so the corpus did not.

    The cost was not hypothetical: `m20_consolidate_dispersion_arms.py` has to
    re-open each arm's `round_report.json` to staple the offset back on, and
    carries an explicit `offset_source` (`round_meta` / `unavailable`) because a
    row whose report is gone can then only be recorded as UNKNOWN. That recovery
    stays — it is the only way to read the 234 already-committed rows — but it
    must not be how NEW rows get their offset.

    Asserted as a KEY, because 0 must read as "the control arm" and never as
    "we did not record it": the train call deliberately does not forward
    `--fold-offset 0` (it is falsy, and the unshifted partition is the trainer's
    default), which is a fact about the argv, not about the measurement.
    """
    src = DRIVER.read_text()
    write_site = '(out / "rounds.jsonl").write_text'
    block = src[src.index("rows.append({"):src.index(write_site)]
    assert '"fold_offset"' in block, (
        "the evidence row no longer records fold_offset, so two rows measured "
        "under different fold partitions — the study's whole independent "
        "variable — are indistinguishable in the corpus")
    assert '"fold_offset": a.fold_offset' in block, (
        "fold_offset is on the row but is not the round's actual argument; a "
        "hardcoded or re-derived value can disagree with what was passed to "
        "the trainer, which is worse than the field being absent")


def test_the_emitted_schema_matches_the_committed_evidence_file() -> None:
    """A row the driver writes must slot into the committed file unchanged."""
    if not EVIDENCE.is_file():
        return  # nothing to match against yet; not a failure
    import json

    rows = [json.loads(x) for x in EVIDENCE.read_text().splitlines() if x.strip()]
    assert rows, "the evidence file is empty — nothing to match"
    committed = set(rows[0])

    spec = importlib.util.spec_from_file_location("_ehr", DRIVER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_ehr"] = mod
    spec.loader.exec_module(mod)

    src = DRIVER.read_text()
    i = src.index('rows.append({')
    emitted_block = src[i:src.index("})", i)]
    emitted = {ln.split('"')[1] for ln in emitted_block.splitlines()
               if ln.strip().startswith('"')}
    missing = committed - emitted
    assert not missing, (
        f"the driver would emit rows missing fields the committed evidence "
        f"file carries: {sorted(missing)}"
    )
