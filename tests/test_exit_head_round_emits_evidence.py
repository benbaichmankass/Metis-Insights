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
    src = DRIVER.read_text()
    i = src.index("rounds.jsonl")
    block = src[max(0, i - 3000):i]
    assert '"tp_geometry": geometry' in block, (
        "the evidence rows stamp something other than the derived geometry; "
        "a scalp round would write live_parity for a cap it never received"
    )


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
