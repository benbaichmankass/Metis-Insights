"""A hand-transcribed round row must record the family the CODE computes.

`docs/research/m20-exit-head-rounds.jsonl` is the graded-round record. Its
`family` field has exactly one definition — `m20_exit_head_round.py` writes
`info.get("family")`, which is set from `m20_fleet_exit_sweep.classify(leg)`.
A row that records anything else is stating a value nothing computed.

Measured 2026-08-15: **10 of 33 rows** were wrong, every `trend_donchian*` row
in the corpus. Each recorded `family: "trend_donchian"` where `classify()`
returns `"donchian"` — because `classify` matches the SUBSTRING `"donchian"`
(`m20_fleet_exit_sweep.py:134`), so the leg id and the family name are not the
same string and transcribing the former produced the latter's near-miss. All ten
carry a hand-transcription provenance (`"relays #NNNN launch / ..."`), never
`"driver-emitted"`; every driver-emitted donchian row says `"donchian"`.

⚠️ WHY THIS IS NOT COSMETIC, even though no verdict moved. The cap decision is
made at RUN time from the live `classify()` call inside `base_args`, never from
this file, so the ten rounds did receive the TP cap and their numbers stand
(relay #9156's geometry probe gated the launch on exactly that). The damage is
to the READER: `LIVE_TP_CAPPED_FAMILIES` is `{"donchian", "pullback", "fade",
"squeeze"}`, so anyone filtering the corpus by `family in
LIVE_TP_CAPPED_FAMILIES` to ask "which rows ran at live-parity TP" gets **every
donchian row answering NO** — reading ten correctly-capped rounds as
`NO_TAKE_PROFIT` books, which is a geometry production does not run. The field
names a quantity the code never computed: CLAUDE.md § "Diagnostic provenance"
sub-class **A**.

It also caught me. Planning the #9436 dispersion screen I read `family:
"trend_donchian"`, saw it was absent from `LIVE_TP_CAPPED_FAMILIES`, and briefly
concluded the prop round had run UNCAPPED — an error I only avoided by reading
`classify()` instead of trusting the record. That is the failure this test
exists to stop, and it is the reason the fix is a guard rather than a one-time
correction: the next hand-transcribed row is one relay away.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))

from m20_fleet_exit_sweep import LIVE_TP_CAPPED_FAMILIES, classify  # noqa: E402

# Kept on ONE line — `test_pytest_run_filter.py` scans tests/ line by line for a
# docs/ path joined onto the repo root, and a wrapped join truncates to the
# directory. This filename is individually named in that filter's list.
ROUNDS = REPO / "docs" / "research" / "m20-exit-head-rounds.jsonl"


def _rows() -> list[dict]:
    return [json.loads(x) for x in ROUNDS.read_text().splitlines() if x.strip()]


# --------------------------------------------------------------------------
# Positive controls FIRST. A corpus that parsed to nothing, or a `classify`
# that returned None for everything, would make the real assertion vacuously
# true — a negative needs a denominator.
# --------------------------------------------------------------------------

def test_the_corpus_is_non_trivial_and_classify_resolves_it() -> None:
    rows = _rows()
    assert len(rows) >= 30, f"expected the committed round corpus, found {len(rows)}"
    resolved = [r for r in rows if classify(r["leg"])]
    assert len(resolved) == len(rows), (
        "classify() returned None for some leg, so those rows could never "
        f"disagree and the check below would be partly vacuous: "
        f"{sorted({r['leg'] for r in rows if not classify(r['leg'])})}")


def test_the_check_can_actually_fire() -> None:
    """Proves the comparison is live — a planted wrong row IS caught.

    Without this, a bug that made `_disagreements` always return `[]` would
    read as a clean corpus forever.
    """
    planted = {"leg": "trend_donchian_eth", "family": "trend_donchian"}
    assert classify(planted["leg"]) != planted["family"], (
        "the planted-wrong row now AGREES with classify(), so this test no "
        "longer proves the comparison fires — if classify() was deliberately "
        "changed to return 'trend_donchian', re-derive the whole module")


# --------------------------------------------------------------------------
# The rule.
# --------------------------------------------------------------------------

def test_every_row_records_the_family_the_code_computes() -> None:
    bad = [(r["leg"], r.get("family"), classify(r["leg"]))
           for r in _rows()
           if r.get("family") != classify(r["leg"])]
    assert not bad, (
        f"{len(bad)} row(s) record a family the driver would not have written. "
        "The driver sets this field from classify(leg); a hand-transcribed row "
        "must match it. Offenders (leg, recorded, computed): "
        f"{sorted(bad)}")


def test_no_row_is_pushed_out_of_the_capped_set_by_a_wrong_family() -> None:
    """The consequence, asserted directly rather than left as an inference.

    A donchian row whose family reads `trend_donchian` silently leaves
    `LIVE_TP_CAPPED_FAMILIES`, so a reader filtering on that set sees a capped
    round as an uncapped one. Pinning the consequence means a future rename of
    either the family strings or the set still fails HERE, next to the reason,
    instead of quietly changing what the corpus appears to say.
    """
    misfiled = [
        r["leg"] for r in _rows()
        if classify(r["leg"]) in LIVE_TP_CAPPED_FAMILIES
        and r.get("family") not in LIVE_TP_CAPPED_FAMILIES
    ]
    assert not misfiled, (
        f"{len(misfiled)} round(s) genuinely ran a capped family but record a "
        "family absent from LIVE_TP_CAPPED_FAMILIES, so filtering the corpus "
        "on that set reads them as NO_TAKE_PROFIT books — a geometry "
        f"production does not run: {sorted(misfiled)}")
