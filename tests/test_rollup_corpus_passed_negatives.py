"""The roll-up must PRINT how many negative cells the sweep corpus has passed.

BL-20260906-MATRIX-STATUS-COLLAPSES-A-PASSING-SWEEP-CELL-INTO-HONEST-NEGATIVE-RECURRENCE-AT-35
is a RECURRENCE — its predecessor measured 9 on 2026-08-14, was marked resolved,
and the number was 35 three weeks later. Its criterion is not "fix the statuses"
(a live leg's disposition is Tier-3 and every disagreement is already
acknowledged in the cell's own ref prose); it is that a roll-up run PRINTS the
count "so it can never again be invisible to an aggregate".

⚠️ THE CONTROLS THEREFORE GRADE THE PRINTING AND THE POPULATION, not the
verdicts. A count computed correctly and shown to nobody is the exact state the
row describes.

⚠️ THE RENDER FIXTURES ARE A REAL ROLL-UP WITH ONE KEY OVERRIDDEN, never a
hand-built dict. A hand-built stub silently stops exercising `render` the moment
`render` reads a key the stub does not carry, and the failure reads as a broken
test rather than as a missing control.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "ci" / "check_matrix_corpus_agreement.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_m20_rollup", REPO / "scripts" / "research" / "m20_coverage_rollup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _real_rollup(m) -> dict:
    """Every key `render` reads, from the module's own producer."""
    return m.rollup(m.load(m.MATRIX))


def _line(out: str, needle: str) -> str:
    hits = [ln for ln in out.splitlines() if needle in ln]
    assert hits, f"no line containing {needle!r} in:\n{out}"
    return hits[0]


def _section(out: str) -> str:
    """Just the corpus-passed block.

    ⚠️ SCOPED DELIBERATELY. An earlier draft asserted `"Tier-3" in out` over the
    WHOLE render and a planted defect that DELETED the adjudication caveat
    stayed green — the phrase occurs elsewhere in the roll-up. A control whose
    needle the rest of the page also supplies grades nothing.
    """
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if "corpus-passed negatives" in ln)
    end = next((i for i, ln in enumerate(lines[start + 1:], start + 1)
                if "per-lever open cells" in ln), len(lines))
    return "\n".join(lines[start:end])


# `trend_donchian` resolves to the `donchian` family, which IS in
# TP_PARITY_AFFECTED_FAMILIES, and `trail_decay` IS in
# GEOMETRY_SENSITIVE_LEVERS — both are required for `evidence_vintage` to grade
# the cell at all. A synthetic leg name resolves to family None, which makes
# `evidence_vintage` bail with classifier_available=False, and a differential
# built on THAT would pass for the wrong reason.
LEG, LEVER = "trend_donchian", "trail_decay"


def _matrix(status="honest_negative", *, geometry="live_parity", ref="2026-08-20"):
    cell = {"status": status, "ref": ref}
    if geometry is not None:
        cell["tp_geometry"] = geometry
    return {"updated_at": "2026-09-12", "lever_columns": [LEVER],
            "rows": [{"strategy": LEG, "symbol": "BTCUSDT", "tf": "1h",
                      "execution": "live", LEVER: cell}]}


def _corpus_row(**over) -> dict:
    """A row that clears the guard's OWN two admissibility filters."""
    row = {"leg": LEG, "lever": LEVER, "cell": "td_t2", "run_id": "2026-09-01-a",
           "verdict": "PASS", "tp_cap_pct": 0.099, "base_trades_OOS": 40}
    row.update(over)
    return row


#: Linked into every fixture tree because `_family_of` resolves the harness
#: family by importing THIS module out of `REPO/scripts/research`. Without it
#: the import fails, `evidence_vintage` reports `classifier_available: False`
#: and returns NO stale cells — so a differential asserting "the stale
#: aggregate does not see this" would pass because nothing was classified at
#: all, which is the wrong reason. The positive control in the differential
#: test is what caught exactly that.
SWEEP = REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py"


def _tree(tmp_path, corpus_rows, *, with_corpus=True, with_guard=True):
    """A REPO-shaped tree carrying the REAL guard and the REAL family
    classifier, so the predicates under test are the ones CI enforces rather
    than a restatement of them."""
    (tmp_path / "scripts" / "ci").mkdir(parents=True)
    if with_guard:
        (tmp_path / "scripts" / "ci" / "check_matrix_corpus_agreement.py").write_text(
            GUARD.read_text(encoding="utf-8"), encoding="utf-8")
    # SYMLINKED, not copied: `m20_fleet_exit_sweep` imports siblings out of the
    # same directory, so a lone copy raises ModuleNotFoundError and lands right
    # back in the `classifier_available: False` hole this exists to avoid.
    (tmp_path / "scripts" / "research").symlink_to(SWEEP.parent)
    (tmp_path / "docs" / "research").mkdir(parents=True)
    if with_corpus:
        (tmp_path / "docs" / "research" / "m20-sweep-corpus.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in corpus_rows), encoding="utf-8")
    return tmp_path


def test_a_missing_corpus_is_unavailable_and_never_zero(tmp_path, monkeypatch):
    """`we could not look` and `nothing passed` are opposite claims, and the
    second is the one that lets a real count stay invisible."""
    m = _load()
    monkeypatch.setattr(m, "REPO", _tree(tmp_path, [], with_corpus=False))
    got = m.negative_cells_the_corpus_passed(_matrix())
    assert got["state"] == m.CORPUS_PASS_UNAVAILABLE
    assert got["count"] is None, "an unavailable read must not report 0"
    assert got["negatives"] is None
    # POSITIVE CONTROL for the same fixture: with the corpus present the same
    # call reads `measured`, so `unavailable` above is the missing file and not
    # a tree this test simply cannot measure anything in.
    monkeypatch.setattr(m, "REPO", _tree(tmp_path / "b", [_corpus_row()]))
    assert m.negative_cells_the_corpus_passed(_matrix())["state"] \
        == m.CORPUS_PASS_MEASURED


def test_a_missing_guard_is_unavailable_too(tmp_path, monkeypatch):
    """The predicates are the guard's, IMPORTED. With no guard to import from
    there is no verdict to report — not a clean zero."""
    m = _load()
    monkeypatch.setattr(
        m, "REPO", _tree(tmp_path, [_corpus_row()], with_guard=False))
    got = m.negative_cells_the_corpus_passed(_matrix())
    assert got["state"] == m.CORPUS_PASS_UNAVAILABLE and got["count"] is None


def test_the_count_is_over_the_whole_population_not_only_stale_cells(
        tmp_path, monkeypatch):
    """THE DEFECT, as a differential against the aggregate that already existed.

    `stale_corpus_state` asks the same question over `evidence_vintage`'s STALE
    cells only. A cell whose evidence is declared live-parity is NOT stale, so a
    fresh-evidence negative the corpus has since passed appeared in no aggregate
    at all.
    """
    m = _load()
    monkeypatch.setattr(m, "REPO", _tree(tmp_path, [_corpus_row()]))
    fresh = _matrix(geometry="live_parity")

    # The pre-existing aggregate cannot see it: the cell is not stale.
    assert m.evidence_vintage(fresh)["stale_cells"] == []
    assert m.stale_corpus_state(fresh)["rows"] == []
    # The new one does.
    got = m.negative_cells_the_corpus_passed(fresh)
    assert got == {"state": m.CORPUS_PASS_MEASURED, "count": 1, "negatives": 1,
                   "rows": [{"leg": LEG, "lever": LEVER,
                             "status": "honest_negative", "corpus_cell": "td_t2",
                             "corpus_verdict": "PASS",
                             "corpus_run": "2026-09-01", "corpus_base_oos": 40}]}

    # POSITIVE CONTROL ON THE SAME FIXTURE — the only change is the declared
    # geometry. Stale, the OLD aggregate does see it, which is what makes the
    # assertions above a statement about the population split rather than about
    # a fixture nothing could ever have graded.
    stale = _matrix(geometry="no_take_profit")
    assert len(m.evidence_vintage(stale)["stale_cells"]) == 1
    rows = m.stale_corpus_state(stale)["rows"]
    assert [r["state"] for r in rows] == [m.CORPUS_DISAGREES]
    assert m.negative_cells_the_corpus_passed(stale)["count"] == 1


def test_only_negative_cells_count_and_the_guards_floors_are_honoured(
        tmp_path, monkeypatch):
    """Three ways the count could quietly become a different quantity."""
    m = _load()
    monkeypatch.setattr(m, "REPO", _tree(tmp_path, [_corpus_row()]))

    # (a) a NON-negative cell with the same passing row is not a finding, and
    #     is not in the denominator either.
    shipped = m.negative_cells_the_corpus_passed(_matrix(status="shipped"))
    assert (shipped["count"], shipped["negatives"]) == (0, 0)

    # (b) the guard's own OOS floor: a pass measured below it is not a graded
    #     answer, and must not be counted as one.
    monkeypatch.setattr(
        m, "REPO", _tree(tmp_path / "oos", [_corpus_row(base_trades_OOS=3)]))
    low = m.negative_cells_the_corpus_passed(_matrix())
    assert (low["count"], low["negatives"]) == (0, 1), \
        "a sub-floor row was counted — the guard's predicate was restated, not imported"

    # (c) the guard's live-TP-parity filter: a legacy no-TP row graded a bracket
    #     production does not run.
    monkeypatch.setattr(
        m, "REPO", _tree(tmp_path / "tp", [_corpus_row(tp_cap_pct=0.0)]))
    legacy = m.negative_cells_the_corpus_passed(_matrix())
    assert (legacy["count"], legacy["negatives"]) == (0, 1)


def test_render_prints_the_count_on_a_default_run():
    """WIRING, and the criterion in its own words. A count computed and not
    printed leaves the row exactly where it was."""
    m = _load()
    r = dict(_real_rollup(m), corpus_passed_negatives={
        "state": m.CORPUS_PASS_MEASURED, "count": 7, "negatives": 31, "rows": [
            {"leg": LEG, "lever": LEVER, "status": "honest_negative",
             "corpus_cell": "td_t2", "corpus_verdict": "PASS",
             "corpus_run": "2026-09-01", "corpus_base_oos": 40}]})
    out = m.render(r)
    line = _line(out, "corpus-passed negatives")
    assert "7" in line and "31" in line, line
    sect = _section(out)
    assert "Tier-3" in sect, \
        "the print must refuse to read as a disposition"
    assert "--corpus-passed-negatives" in sect, \
        "a count with no way to list the rows sends the reader nowhere"

    # A real zero over a real population still PRINTS — silence there is the
    # same invisibility one value down.
    zero = m.render(dict(r, corpus_passed_negatives={
        "state": m.CORPUS_PASS_MEASURED, "count": 0, "negatives": 31,
        "rows": []}))
    assert "0" in _line(zero, "corpus-passed negatives")
    assert "Tier-3" not in _section(zero), \
        "the adjudicate caveat belongs to a non-zero count"


def test_render_says_could_not_look_rather_than_zero():
    m = _load()
    out = m.render(dict(_real_rollup(m), corpus_passed_negatives={
        "state": m.CORPUS_PASS_UNAVAILABLE, "count": None, "negatives": None,
        "why": "resolver_present=True corpus_present=False", "rows": []}))
    line = _line(out, "corpus-passed negatives")
    assert "COULD NOT LOOK" in line, line
    assert "NOT zero" in line, line
    assert "corpus_present=False" in line, "the reason must ride the line"


def test_the_rollup_dict_carries_the_block():
    """It must ride the same dict `--json` emits: a number available only in
    prose is one an aggregate still cannot read."""
    m = _load()
    got = _real_rollup(m)
    assert "corpus_passed_negatives" in got, sorted(got)
    cp = got["corpus_passed_negatives"]
    assert cp["state"] in (m.CORPUS_PASS_MEASURED, m.CORPUS_PASS_UNAVAILABLE)
    if cp["state"] == m.CORPUS_PASS_MEASURED:
        assert isinstance(cp["count"], int) and isinstance(cp["negatives"], int)
        assert cp["count"] <= cp["negatives"], "a subset cannot exceed its set"
    # and it is PRINTED by the same run, not only carried.
    assert "corpus-passed negatives" in m.render(got)
