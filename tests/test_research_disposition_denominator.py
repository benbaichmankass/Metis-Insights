"""A reported statistic must carry its own denominator, and its invariant must be pinned.

WHY THIS EXISTS
---------------
`research_disposition.survey()` reports `n_oos` per unit and prints it on the
same line as `rows`. Those two numbers are NOT a statistic and its population —
they are a statistic and an unrelated, much larger, count.

MEASURED 2026-09-02 over the committed stores at 943a7192 (population: all 315
units of the two power-graded corpora, e35 97 + m20 218):

  * 258 units carry at least one achieved OOS count (41 e35, 217 m20).
  * On EVERY ONE of those 258, the values across the unit's rows are IDENTICAL —
    units where `n_oos` varies: **0**. That constancy is what makes `max()` in
    `load_units` correct, and it is why the neighbouring `power_state` reducer's
    "worst-state wins" discipline is not in conflict with it: on this population
    max(), min() and worst-state-wins return the same number 258 times out of
    258. Swapping max() for min() would change no output.
  * The coverage is the part that IS a live hazard. On e35 the count is carried
    by **7 of a unit's 199 rows — 3.52%, on 41 of 41** units that have one. The
    report printed `rows=199 n_oos=49` side by side, and 199 is 28x the actual
    denominator.

So the defect was never the reducer; it was the missing population. This module
pins BOTH halves, because they fail for different reasons:

  1. the CONSTANCY that makes the reducer safe — if it ever breaks, `max()`
     silently becomes a real choice and the docstring's reasoning expires;
  2. the DENOMINATOR being carried at all — `rows_with_n` present and non-zero
     wherever `n_oos` is non-null.

⚠️ EVERY ASSERTION HERE IS PROVEN TO FIRE BY A PLANTED CONTROL. A test over a
live store that has never been shown to fail is indistinguishable from one whose
predicate cannot fail — the vacuous-join shape this repo has already paid for.
Each `test_*_control` below plants the defect and asserts the checker REJECTS
it; the live-store tests then assert the real data passes the same checker.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.research import research_disposition as rd  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# The checkers. Pure, so a control can be planted against them.
# ─────────────────────────────────────────────────────────────────────────────
def n_oos_dispersion(path: Path, stamp_field: str, leg_field: str, n_field: str):
    """(units_with_a_value, units_where_it_varies, min_coverage_fraction).

    Reads the store directly rather than through `load_units`, deliberately:
    `load_units` REDUCES with max(), so asking it whether the values varied is
    asking the reducer to report on itself.
    """
    vals: dict = {}
    rows: dict = {}
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            stamp, leg = row.get(stamp_field), row.get(leg_field)
            if not stamp or not leg:
                continue
            rows[(stamp, leg)] = rows.get((stamp, leg), 0) + 1
            v = row.get(n_field)
            if isinstance(v, (int, float)):
                vals.setdefault((stamp, leg), []).append(int(v))
    with_val = [k for k in rows if vals.get(k)]
    varying = [k for k in with_val if len(set(vals[k])) > 1]
    cov = min((len(vals[k]) / rows[k]) for k in with_val) if with_val else None
    return len(with_val), len(varying), cov


def assert_denominator_carried(units: list) -> None:
    """Every unit reporting an `n_oos` must also report how many rows gave it.

    ⚠️ THE ZERO CASE IS THE POINT. `rows_with_n == 0` beside a non-null `n_oos`
    is not a small inconsistency — it is a statistic asserting a population that
    does not exist, which is the fabrication class `provenance.py` exists to
    stop, one layer up.
    """
    for u in units:
        if u.get("n_oos") is None:
            continue
        if "rows_with_n" not in u:
            raise AssertionError(
                f"{u.get('corpus')}/{u.get('leg')} reports n_oos="
                f"{u['n_oos']} with NO denominator field")
        if not u["rows_with_n"] > 0:
            raise AssertionError(
                f"{u.get('corpus')}/{u.get('leg')} reports n_oos={u['n_oos']} "
                f"over rows_with_n={u['rows_with_n']} — a value over an empty "
                "population is not a measurement")


# ─────────────────────────────────────────────────────────────────────────────
# CONTROLS — each plants the defect and asserts the checker rejects it.
# ─────────────────────────────────────────────────────────────────────────────
def test_dispersion_control_a_varying_unit_is_detected(tmp_path):
    """POSITIVE CONTROL for the constancy probe: it can see a varying unit.

    Without this, `varying == 0` on the live corpus proves only that the probe
    is quiet — which is not the same as the corpus being constant.
    """
    store = tmp_path / "c.jsonl"
    store.write_text("".join(json.dumps(r) + "\n" for r in [
        {"sweep_generated_at": "T1", "leg": "a", "base_oos_trades": 50},
        {"sweep_generated_at": "T1", "leg": "a", "base_oos_trades": 90},  # varies
    ]))
    with_val, varying, _cov = n_oos_dispersion(
        store, "sweep_generated_at", "leg", "base_oos_trades")
    assert with_val == 1
    assert varying == 1, "the probe cannot see a unit whose n_oos varies"


def test_dispersion_control_a_constant_unit_reads_as_constant(tmp_path):
    """NEGATIVE CONTROL: the probe does not cry wolf on a genuinely flat unit."""
    store = tmp_path / "c.jsonl"
    store.write_text("".join(json.dumps(r) + "\n" for r in [
        {"sweep_generated_at": "T1", "leg": "a", "base_oos_trades": 50},
        {"sweep_generated_at": "T1", "leg": "a", "base_oos_trades": 50},
    ]))
    _w, varying, cov = n_oos_dispersion(
        store, "sweep_generated_at", "leg", "base_oos_trades")
    assert varying == 0
    assert cov == 1.0


def test_denominator_control_a_missing_field_is_rejected():
    """The pre-fix shape: an `n_oos` with no `rows_with_n` beside it."""
    with pytest.raises(AssertionError, match="NO denominator"):
        assert_denominator_carried([{"corpus": "e35", "leg": "x", "n_oos": 49}])


def test_denominator_control_a_zero_denominator_is_rejected():
    """A value over an empty population must not read as a clean measurement."""
    with pytest.raises(AssertionError, match="empty population"):
        assert_denominator_carried(
            [{"corpus": "e35", "leg": "x", "n_oos": 49, "rows_with_n": 0}])


def test_denominator_control_a_well_formed_unit_passes():
    assert_denominator_carried(
        [{"corpus": "e35", "leg": "x", "n_oos": 49, "rows_with_n": 7},
         {"corpus": "gld_compat", "leg": "y", "n_oos": None}])


# ─────────────────────────────────────────────────────────────────────────────
# THE LIVE STORES — the same checkers, now against the committed corpora.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("corpus", ["e35", "m20"])
def test_n_oos_is_a_leg_level_constant_in_the_live_corpus(corpus):
    """The invariant that makes `max()` correct, asserted rather than assumed.

    If this ever fails, `max()` has become a real choice between real
    alternatives and the reducer needs a decision — not a widening of this test.
    """
    path, stamp_field, leg_field = rd.CORPORA[corpus]
    n_field = rd.N_FIELD[corpus]
    assert n_field, f"{corpus} carries no n field; this test would be vacuous"
    if not path.exists():
        pytest.skip(f"{corpus} corpus absent")
    with_val, varying, _cov = n_oos_dispersion(
        path, stamp_field, leg_field, n_field)
    # ⚠️ NON-VACUITY. `varying == 0` over an empty population is not a pass.
    assert with_val > 0, (
        f"{corpus}: no unit carries {n_field} — the constancy assertion below "
        "would be vacuously true")
    assert varying == 0, (
        f"{corpus}: {varying}/{with_val} units have a VARYING {n_field}. "
        "`load_units` reduces with max(), which was justified on this being a "
        "leg-level constant. That justification has expired — decide between "
        "max/min/worst-state-wins on the merits before widening this test.")


def test_every_reported_n_oos_carries_its_denominator():
    """The whole live survey, through the real `survey()`."""
    s = rd.survey()
    assert rd.CORPUS_UNREADABLE not in (s["ledger_state"], *s["corpora"].values()), (
        "a store was unreadable — this run cannot certify anything")
    units = s["units"]
    assert units, "survey returned no units — this test would be vacuous"
    graded = [u for u in units if u["n_oos"] is not None]
    assert graded, "no unit is power-graded — the assertion below would be vacuous"
    assert_denominator_carried(units)


def test_the_report_line_does_not_print_n_oos_bare_beside_rows():
    """`rows` must not be the only count on the line carrying `n_oos`.

    A field-level assertion, not a string check on the output: the hazard is a
    reader taking `rows` for the denominator, and what removes it is the
    denominator being present in the record the line is rendered from.
    """
    s = rd.survey(corpora=["e35"])
    graded = [u for u in s["units"] if u["n_oos"] is not None]
    assert graded, "no graded e35 unit — vacuous"
    # MEASURED 2026-09-02: 7 of 199 on all 41 graded e35 units. The point is
    # that the two differ, not the specific ratio, so this asserts the gap
    # rather than pinning 7/199 — a re-sweep may legitimately move both.
    mismatched = [u for u in graded if u["rows_with_n"] != u["rows"]]
    assert mismatched, (
        "every graded e35 unit now has rows_with_n == rows. That would make "
        "`rows` a correct denominator and this hazard would be gone — re-read "
        "the load_units docstring before deleting anything.")
    u = mismatched[0]
    assert u["rows_with_n"] < u["rows"]


# ─────────────────────────────────────────────────────────────────────────────
# The supersession partition — the second collapsed count.
# ─────────────────────────────────────────────────────────────────────────────
def test_partition_control_a_gap_is_detected():
    """POSITIVE CONTROL: a leg whose newest run is unread reads as a GAP."""
    s = {"units": [
        {"corpus": "m20", "leg": "a", "run_stamp": "T1",
         "state": rd.SUPERSEDED_UNREAD, "superseded_by": "T2",
         "superseded_by_state": rd.UNREAD},
    ]}
    p = rd.partition_superseded(s)
    assert p[rd.SUPERSEDED_GAP] == 1 and p[rd.SUPERSEDED_BENIGN] == 0
    assert p["gap_legs"] == [("m20", "a", "T2")]


def test_partition_control_a_read_successor_is_benign():
    """NEGATIVE CONTROL: the ordinary re-measurement residue is NOT a finding."""
    s = {"units": [
        {"corpus": "m20", "leg": "a", "run_stamp": "T1",
         "state": rd.SUPERSEDED_UNREAD, "superseded_by": "T2",
         "superseded_by_state": rd.DISPOSITIONED},
    ]}
    p = rd.partition_superseded(s)
    assert p[rd.SUPERSEDED_BENIGN] == 1 and p[rd.SUPERSEDED_GAP] == 0


def test_partition_control_the_two_halves_are_distinguishable():
    """If these ever collapsed, the partition would report one number again —
    which is the state the row that motivated it was filed against."""
    assert rd.SUPERSEDED_BENIGN != rd.SUPERSEDED_GAP


def test_partition_of_the_live_stores_sums_and_is_not_vacuous():
    s = rd.survey()
    p = rd.partition_superseded(s)
    assert p["total"] > 0, "nothing is superseded — the partition would be vacuous"
    assert p[rd.SUPERSEDED_BENIGN] + p[rd.SUPERSEDED_GAP] == p["total"]
    # ⚠️ NOT an assertion that the gap is zero. It is 33 at 943a7192 and that is
    # a finding, not a failure; this pins only that BOTH halves were computed
    # from a real successor state rather than defaulted.
    for u in s["units"]:
        if u["state"] == rd.SUPERSEDED_UNREAD:
            assert u["superseded_by"], f"{u['leg']} superseded by nothing"
            assert u["superseded_by_state"] in rd.STATES, u["superseded_by_state"]
