"""A `--fold-offset` dispersion ARM must never be counted as a graded ROUND.

`docs/research/m20-exit-head-rounds.jsonl` is the graded-round record, and both
of its consumers in `m20_exit_head_denominator.py` assume one row per
leg-measurement:

  * `_report_negative_column_vintage` builds `{leg: row}` — the LAST row for a
    leg silently wins, so an arm appended after a leg's graded round would
    become the measurement that leg is judged against.
  * `_report_live_parity_rounds` pools every row into a per-geometry flip rate —
    six legs at five offsets is thirty rows that look exactly like thirty
    independent graded rounds, inflating a denominator that is quoted as
    evidence.

Neither would raise. Neither would print anything unusual. The corrupted flip
rate would simply be a different number, reported with the same confidence — the
failure mode the whole `m20_exit_head_denominator.py` file exists to avoid one
level down (it refuses to pool two TP geometries for the same reason).

The schema has carried `fold_offset` since the flag shipped, and no consumer read
it until 2026-08-15. A field written and never read is what lets the
contaminating row in unnoticed; these tests are the read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))

import m20_exit_head_denominator as den  # noqa: E402

BASELINE = {"leg": "iaum_pullback_1d", "mean_auc": 0.5525, "verdict": "candidate",
            "tp_geometry": "live_parity", "tf": "1d", "fold_offset": None}
ARM = {"leg": "iaum_pullback_1d", "mean_auc": 0.4903, "verdict": "honest_negative",
       "tp_geometry": "live_parity", "tf": "1d", "fold_offset": 24}


def _write(tmp_path: Path, rows: list[dict], monkeypatch) -> Path:
    p = tmp_path / "rounds.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(den, "ROUNDS", p)
    return p


def test_a_nonzero_offset_row_is_excluded(tmp_path, monkeypatch) -> None:
    _write(tmp_path, [BASELINE, ARM], monkeypatch)
    kept = den._load_graded_rounds()
    assert len(kept) == 1, kept
    assert kept[0]["fold_offset"] in (None, 0)


def test_null_and_zero_both_count_as_baseline(tmp_path, monkeypatch) -> None:
    """`null` is a round predating the flag; `0` is an explicit baseline arm.

    Neither shifts a boundary, so both are graded rounds. Dropping `null` would
    discard all 33 rounds committed before the flag existed.
    """
    rows = [dict(BASELINE, leg="a", fold_offset=None),
            dict(BASELINE, leg="b", fold_offset=0),
            dict(ARM, leg="c", fold_offset=6)]
    _write(tmp_path, rows, monkeypatch)
    assert sorted(r["leg"] for r in den._load_graded_rounds()) == ["a", "b"]


def test_the_leg_keyed_lookup_WOULD_be_corrupted_without_the_filter(
        tmp_path, monkeypatch) -> None:
    """The point of the filter, demonstrated rather than asserted.

    A plain last-wins dict over the same file resolves `iaum_pullback_1d` to the
    ARM's `honest_negative`; the filtered load resolves it to the graded
    `candidate`. Same file, opposite verdict, and nothing in the unfiltered path
    signals that a substitution happened.
    """
    p = _write(tmp_path, [BASELINE, ARM], monkeypatch)

    naive = {json.loads(x)["leg"]: json.loads(x)
             for x in p.read_text().splitlines() if x.strip()}
    assert naive["iaum_pullback_1d"]["verdict"] == "honest_negative"

    guarded = {r["leg"]: r for r in den._load_graded_rounds()}
    assert guarded["iaum_pullback_1d"]["verdict"] == "candidate"


def test_the_pooled_denominator_WOULD_be_inflated_without_the_filter(
        tmp_path, monkeypatch) -> None:
    """Five arms of one leg must not read as five independent rounds."""
    rows = [BASELINE] + [dict(ARM, fold_offset=k) for k in (6, 12, 18, 24)]
    _write(tmp_path, rows, monkeypatch)
    assert len(rows) == 5
    assert len(den._load_graded_rounds()) == 1


def test_the_exclusion_is_ANNOUNCED_not_silent(tmp_path, monkeypatch, capsys) -> None:
    """A filter that drops rows quietly trades one silent corruption for another.

    The reader must be able to see that the file contains rows the analysis did
    not count, and which ones.
    """
    _write(tmp_path, [BASELINE, ARM], monkeypatch)
    den._load_graded_rounds()
    out = capsys.readouterr().out
    assert "fold_offset" in out and "EXCLUDED" in out, out
    assert "iaum_pullback_1d" in out and "24" in out, out


def test_a_clean_file_prints_NOTHING(tmp_path, monkeypatch, capsys) -> None:
    """Today every committed row is baseline, so the guard must be inert.

    An always-on warning is the desensitized-alarm shape: it would be walked
    past on every run and would say nothing on the run that mattered.
    """
    _write(tmp_path, [BASELINE, dict(BASELINE, leg="gld_pullback_1d")], monkeypatch)
    den._load_graded_rounds()
    assert capsys.readouterr().out == ""


def test_the_committed_rounds_file_is_currently_all_baseline() -> None:
    """Anchors the claim made in the loader's docstring against the real file.

    If a future session appends arms here, this fails and points at the sibling
    artifact rather than letting the analysis quietly change.
    """
    rows = [json.loads(x) for x in
            (REPO / "docs" / "research" / "m20-exit-head-rounds.jsonl")
            .read_text().splitlines() if x.strip()]
    assert rows, "rounds file is empty — the denominator analysis has no evidence"
    offenders = [(r.get("leg"), r.get("fold_offset")) for r in rows if r.get("fold_offset")]
    assert not offenders, (
        f"dispersion arms found in the graded-rounds file: {offenders}. "
        "They belong in docs/research/m20-fold-dispersion-arms.jsonl")
