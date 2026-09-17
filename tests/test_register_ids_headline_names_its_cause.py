"""`register-id-guard` must headline a finding with the cause it tested.

⚠️ REPRODUCED, NOT CONSTRUCTED. Against `main` @`123f6c192` on 2026-09-17, in a
temp git repo holding TWO DISTINCT ids with nothing colliding, the guard
printed:

    …::items[id]: 2 rows, unique · R2a assessed 1/1 (100%) · R2b 1/1, 0 replaced
    ::error::A register id collided. Git cannot see this class: to git a reused
      id is a changed value at the same key, so the merge silently deletes the
      row that was already there.
      - …: NEW row 'FIXTURE-BETA' records no creation date…

⚠️ THE FIXTURE IDS READ `FIXTURE-…`, NOT `BL-…`, AND THAT IS NOT COSMETIC. The
quote above is the guard's real output with the invented row renamed:
`scripts/ops/check_backlog_refs.py` scans added lines for register ids, and an
INVENTED `BL-`-shaped id resolves to no filed row — reported as "a doc saying
'tracked by BL-X' where BL-X was never filed". Caught on this very change
(artifact-validity-guard, the 1 FAIL of 85). A fixture id must not be shaped
like a real one.

`main` printed ONE `::error::` headline over a `problems` list pooling R1, R2
and R3. **R3 is not a collision** — the census line on the very same run says
`unique`, correctly. UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A.

⚠️ **THE HARM IS THE REMEDY, NOT THE WORDING.** The headline's advice is *mint
a fresh id*; the correct R3 fix is *add the creation field*. A session that
follows the headline mints an id nothing asked for — and on 2026-09-17 the
board was separately warning that this guard's remedy line is wrong for a
byte-identical duplicate, so a reader is already being asked to distrust it.

⚠️ **AND #12398's CENSUS FIX MADE THIS SHARPER RATHER THAN SAFER.** Before it
the census also said `unique` whatever `check_uniqueness` returned, so a reader
could dismiss both lines as one confused guard. The census is now DERIVED and
correct, so a run states a true `unique` beside a false `collided` and the
honest line lends the dishonest one its credibility.

⚠️ **ONE THING THAT WENT THE OTHER WAY, AND IT NARROWED THE FIX.** The obvious
change is one headline per rule. Measured instead: **R2 is correctly
described.** Its own message ends *"That is the signature of a NEW item filed
under an id that was already taken — merging it deletes the row that was
there"*, so `collided` fits R2 exactly. Only R3 comes out. Three headlines
would have been a split the evidence does not support.

This file is the END-TO-END half. `check_register_ids.py --self-test` covers
`group_by_headline` purely (including the untagged-rule and no-findings
controls); only a real git base proves the printing path a CI run reaches.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "ci" / "check_register_ids.py"

COLLIDED = "A register id collided"
UNDATED = "A NEW register row records no creation date"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _register():
    """The first declared register, read from the guard rather than restated."""
    sys.path.insert(0, str(REPO))
    from scripts.ci.check_register_ids import REGISTERS
    return REGISTERS[0]


def _repo_with_base(tmp_path: Path, base_rows) -> Path:
    """A git repo whose HEAD carries `base_rows` in the first register."""
    reg = _register()
    _git(tmp_path, "init", "-q", ".")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    p = tmp_path / reg.path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({reg.array: base_rows}, indent=2), encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _write_head(tmp_path: Path, head_rows) -> None:
    reg = _register()
    (tmp_path / reg.path).write_text(
        json.dumps({reg.array: head_rows}, indent=2), encoding="utf-8")


def _run(tmp_path: Path):
    """`(returncode, stdout)` from the guard, diffed against the base commit."""
    proc = subprocess.run(
        [sys.executable, str(GUARD), "--root", ".", "--base", "HEAD"],
        cwd=tmp_path, capture_output=True, text=True)
    return proc.returncode, proc.stdout


DATED = {"id": "FIXTURE-ALPHA", "opened": "2026-01-01",
         "summary": "the original row"}


def _rows(*rows):
    reg = _register()
    out = []
    for r in rows:
        row = dict(r)
        row[reg.id_field] = row.pop("id")
        if "opened" in row and reg.creation_fields[0] != "opened":
            row[reg.creation_fields[0]] = row.pop("opened")
        out.append(row)
    return out


# ── the defect ─────────────────────────────────────────────────────────────

def test_an_R3_only_run_is_not_headlined_as_a_collision(tmp_path):
    """The reproduction, end to end through `main`."""
    _repo_with_base(tmp_path, _rows(DATED))
    _write_head(tmp_path, _rows(
        DATED, {"id": "FIXTURE-BETA", "summary": "new, NO creation date"}))

    rc, out = _run(tmp_path)
    assert rc == 1, out
    assert "records no creation date" in out, out
    assert COLLIDED not in out, (
        "an R3 finding is filed under the id-collision headline — nothing "
        "collided, and the census on this very run says `unique`:\n" + out)


def test_the_R3_headline_gives_the_R3_remedy(tmp_path):
    """Naming the cause is half; the reader acts on the REMEDY.

    A headline that said only "R3 failed" would pass the test above and still
    leave `mint a fresh id` as the last instruction the reader saw.
    """
    _repo_with_base(tmp_path, _rows(DATED))
    _write_head(tmp_path, _rows(
        DATED, {"id": "FIXTURE-BETA", "summary": "new, NO creation date"}))
    _, out = _run(tmp_path)
    headline = next(ln for ln in out.splitlines() if UNDATED in ln)
    assert "Do NOT mint a fresh id" in headline, headline
    assert "ADD the creation field" in headline, headline


def test_the_census_still_reports_unique_on_that_same_run(tmp_path):
    """The two lines must now AGREE.

    The census being right is what made the old headline dangerous; this pins
    that the fix did not "resolve" the contradiction by degrading the census.
    """
    _repo_with_base(tmp_path, _rows(DATED))
    _write_head(tmp_path, _rows(
        DATED, {"id": "FIXTURE-BETA", "summary": "new, NO creation date"}))
    _, out = _run(tmp_path)
    census = next(ln for ln in out.splitlines() if "2 rows" in ln)
    assert "unique" in census and "UNIQUENESS PROBLEM" not in census, census


# ── the positive controls ──────────────────────────────────────────────────

def test_a_real_collision_is_still_headlined_as_one(tmp_path):
    """THE POSITIVE CONTROL.

    Without it, deleting the collision headline outright would satisfy every
    assertion above while destroying the rule this guard exists for.
    """
    _repo_with_base(tmp_path, _rows(DATED))
    _write_head(tmp_path, _rows(DATED, DATED))
    rc, out = _run(tmp_path)
    assert rc == 1, out
    assert COLLIDED in out, out
    assert UNDATED not in out, (
        "a pure collision printed the no-creation-date headline too — a "
        "heading asserting a cause nothing established:\n" + out)


def test_R2_keeps_the_collision_headline(tmp_path):
    """MEASURED, not assumed — see the module docstring.

    R2's own message says a taken id was reused, so `collided` describes it.
    This pins the narrower fix against a later tidy-up that gives every rule
    its own headline on symmetry grounds.
    """
    _repo_with_base(tmp_path, _rows(DATED))
    _write_head(tmp_path, _rows(
        {"id": "FIXTURE-ALPHA", "opened": "2026-02-02",
         "summary": "an entirely different item"}))
    rc, out = _run(tmp_path)
    assert rc == 1, out
    assert COLLIDED in out, out
    assert UNDATED not in out, out


def test_a_clean_run_prints_no_error_headline_at_all(tmp_path):
    _repo_with_base(tmp_path, _rows(DATED))
    _write_head(tmp_path, _rows(
        DATED, {"id": "FIXTURE-DELTA", "opened": "2026-03-03",
                "summary": "new and dated"}))
    rc, out = _run(tmp_path)
    assert rc == 0, out
    assert "::error::" not in out, out
    assert "register-id-guard: OK" in out, out


def test_a_run_tripping_both_prints_both_headlines(tmp_path):
    """Splitting must not make one cause hide the other."""
    _repo_with_base(tmp_path, _rows(DATED))
    _write_head(tmp_path, _rows(
        DATED, DATED, {"id": "FIXTURE-GAMMA", "summary": "new, undated"}))
    rc, out = _run(tmp_path)
    assert rc == 1, out
    assert COLLIDED in out and UNDATED in out, out
    assert out.index(COLLIDED) < out.index(UNDATED), (
        "HEADLINE_ORDER is fixed so a run tripping both is stable:\n" + out)


# ── no positional cross-reference between the headlines ────────────────────

@pytest.mark.parametrize("word", ["below", "above"])
def test_neither_headline_points_at_the_other_by_position(word):
    """CAUGHT IN MY OWN FIRST DRAFT, and recorded rather than quietly fixed.

    The R3 headline read *"the OPPOSITE of the collision one BELOW"*, which is
    wrong in both directions a run can take: the collision block prints FIRST
    when both trip, and on the R3-only run this split exists for there is no
    collision block at all. A pointer to a neighbouring block that may not
    exist is the same sub-class A defect one hop down — written while fixing
    it, and found by reading the four arms' OUTPUT rather than the string.
    """
    sys.path.insert(0, str(REPO))
    from scripts.ci.check_register_ids import (
        COLLIDED_HEADLINE,
        UNDATED_HEADLINE,
    )
    for headline in (COLLIDED_HEADLINE, UNDATED_HEADLINE):
        assert word not in headline.lower(), headline
