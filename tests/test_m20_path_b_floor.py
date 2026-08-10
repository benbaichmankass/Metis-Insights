"""The Path B floor analysis + the corpus it reads.

These two scripts exist to answer an operator directive with data
(2026-08-10: *"use optimization of the capital utilization and PnL to decide what
the correct number is ... database decisions and not arbitrary guesses"*), and
their output will be read as the basis of a Tier-3 call. So the properties pinned
here are not about arithmetic — they are about the ways a statistical answer lies:

  1. **"We did not look" never reads as "we looked and found nothing."** A corpus
     too small to test a floor must say `insufficient_population`, never
     `no_separation`. Collapsing them turns an absent measurement into a
     finding — and the finding would be "no floor is needed", which is the
     permissive direction.
  2. **A cell that was never walk-forwarded is not a cell that failed one.**
     Folding absent evidence into the failures manufactures a negative.
  3. **The selection denominator travels with the claim.** Scanning K floors and
     reporting the best p is selection over an unstated denominator; the verdict
     must clear a bar adjusted for K.
  4. **No fabricated precision.** A tiny p must not print as `0.0` (a claim of
     impossibility), and tied floors must be disclosed rather than one of them
     reported as though it had been chosen.
  5. **Legs that produced nothing are still in the corpus.** A leg that errored
     or was skipped is part of the fleet denominator.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


floor = _load("m20_path_b_floor", "scripts/research/m20_path_b_floor.py")
extract = _load("m20_corpus_extract", "scripts/research/m20_corpus_extract.py")


def _cell(leg, rate, wins, usable=6, ran=True, **kw):
    row = {"kind": "cell", "leg": leg, "cell": f"{leg}_{rate}", "base_rate_IS": rate,
           "wf_ran": ran, "wf_wins": wins, "wf_usable": usable}
    row.update(kw)
    return row


def _separating(n=12):
    """Low-rate legs never generalise; high-rate legs always do."""
    return ([_cell(f"lo{i}", 0.30 + 0.01 * i, 1) for i in range(n)]
            + [_cell(f"hi{i}", 2.00 + 0.01 * i, 5) for i in range(n)])


# ---------------------------------------------------------------- 1. the split

def test_a_corpus_too_small_says_we_did_not_look():
    """`insufficient_population`, never `no_separation`.

    This is the whole reason the verdict is three-valued. The permissive reading
    of a small corpus is "no floor is needed", and it is indistinguishable from
    the real finding unless the two are separate states.
    """
    res = floor.analyse([_cell("a", 0.4, 4), _cell("b", 2.0, 4)], "base_rate_IS")
    assert res["verdict"] == "insufficient_population"
    assert res["floors_tried"] == 0
    assert res["recommended_floor"] is None


def test_many_cells_on_few_legs_is_insufficient_not_a_finding():
    """The clustering trap, and the reason this is not just a row count.

    The predictor is a property of the LEG: every cell swept on a leg carries
    that leg's single base rate, so cells within a leg are re-measurements of one
    book under different levers — sharing its trades, regime and drawdown — not
    independent samples. 60 rows over 3 legs is a 3-point comparison wearing a
    60-point denominator, and a test assuming independence would return a tiny p
    for it. That is worse than guessing a floor, because it arrives dressed as
    significance.

    This fixture has plenty of CELLS (30 per arm) and too few LEGS, and must
    still say we did not look. It also exercises the branch that reports WHICH
    scarcity blocked it — the branch a `p`/`pop` name error made raise instead of
    report, caught by ruff rather than by any test here until now.
    """
    rows = ([_cell(f"lo{i % 3}", 0.4, 1) for i in range(30)]
            + [_cell(f"hi{i % 3}", 2.5, 5) for i in range(30)])
    res = floor.analyse(rows, "base_rate_IS")
    assert res["verdict"] == "insufficient_population"
    assert res["floors_rejected_thin_on_legs"] >= 1, (
        "a floor was rejected for thin LEG coverage but the count did not record "
        "it — 'too few legs' and 'no floor testable' need different fixes")
    assert "MORE LEGS" in res["verdict_why"], (
        "the message must name the actionable scarcity, not just 'too small'")


def test_the_rate_carries_its_own_trade_count():
    """A rate over 4 trades is not the claim a rate over 800 is.

    `base_rate` is net_R/maxDD over the leg's base book, and that book can be
    hundreds of trades or a handful — the 2026-08-10 fleet sweep returned Path A
    PASSes on out-of-sample windows of 3, 4 and 5 trades. Quoting "the lowest
    rate is 1.08" without the n behind it is an unstated denominator one level
    below the one this file already guards.

    Reported, never filtered: dropping thin legs would silently redefine the
    population, and WHICH legs are thin is itself part of the answer.
    """
    rows = [_cell(f"lo{i}", 0.4, 1, base_trades_IS=400, base_trades_OOS=90)
            for i in range(6)]
    rows += [_cell(f"hi{i}", 2.5, 5, base_trades_IS=57, base_trades_OOS=3)
             for i in range(6)]
    res = floor.analyse(rows, "base_rate_IS")
    per_leg = res["per_leg"]
    assert len(per_leg) == 12
    assert all("base_trades_IS" in v and "base_trades_OOS" in v
               for v in per_leg.values()), "a rate is reported without its n"
    assert res["axis_distribution"]["base_trades_IS_min"] == 57
    # A leg whose count is absent is COUNTED as absent, not silently treated as
    # large — "we do not know the n" is not "the n is fine".
    res2 = floor.analyse(rows + [_cell("nc", 1.5, 5)], "base_rate_IS")
    assert res2["axis_distribution"]["legs_missing_trade_count"] == 1


def test_leg_counts_ride_in_every_grid_row():
    """A cell count without its leg count restates the clustering problem."""
    res = floor.analyse(_separating(), "base_rate_IS")
    assert res["grid"], "no floors tried"
    for g in res["grid"]:
        assert g["admitted_legs"] >= floor.MIN_LEGS_PER_ARM
        assert g["rejected_legs"] >= floor.MIN_LEGS_PER_ARM
        assert g["admitted_legs"] <= g["admitted_n"]


def test_a_real_relationship_is_found():
    """The negative above is worthless unless the same machinery CAN find one."""
    res = floor.analyse(_separating(), "base_rate_IS")
    assert res["verdict"] == "separation"
    assert res["recommended_floor"] == 2.0, (
        "the separating floor is the bottom of the high cluster; anything lower "
        "admits cells that do not generalise")


def test_no_signal_is_reported_as_having_looked():
    """Pass/fail independent of the rate must be `no_separation`, not silence."""
    rows = [_cell(f"n{i}", 0.3 + 0.1 * i, 5 if i % 2 else 1) for i in range(24)]
    res = floor.analyse(rows, "base_rate_IS")
    assert res["verdict"] == "no_separation"
    assert res["floors_tried"] > 0, "claims to have looked but tried no floor"


# ------------------------------------------------- 2. absent evidence is absent

def test_a_cell_with_no_walkforward_is_excluded_not_failed():
    """Absent generalisation evidence must not be counted as a failure.

    With 20 un-walk-forwarded low-rate cells folded in as failures, the floor
    would appear strongly supported on evidence that does not exist.
    """
    rows = _separating() + [_cell(f"x{i}", 0.35, None, None, ran=False)
                            for i in range(20)]
    res = floor.analyse(rows, "base_rate_IS")
    assert res["population"]["cells_no_walkforward"] == 20
    assert res["population"]["analysed"] == 24, (
        "un-walk-forwarded cells leaked into the analysed population")


def test_wf_pass_returns_none_not_false_when_absent():
    assert floor.wf_pass({"wf_ran": False}) is None
    assert floor.wf_pass({"wf_ran": True, "wf_wins": None, "wf_usable": None}) is None
    assert floor.wf_pass({"wf_ran": True, "wf_wins": 4, "wf_usable": 6}) is True
    # Fewer than the minimum usable folds is a FAIL, not a pass on a thin sample.
    assert floor.wf_pass({"wf_ran": True, "wf_wins": 3, "wf_usable": 3}) is False


def test_rows_missing_the_axis_are_counted_not_dropped():
    rows = _separating() + [{"kind": "cell", "leg": "m", "cell": "x",
                             "wf_ran": True, "wf_wins": 5, "wf_usable": 6}] * 3
    res = floor.analyse(rows, "base_rate_IS")
    assert res["population"]["cells_missing_axis"] == 3


# ------------------------------------------- 3+4. denominator + no false precision

def test_the_verdict_clears_a_bar_adjusted_for_floors_tried():
    res = floor.analyse(_separating(), "base_rate_IS")
    k = res["floors_tried"]
    assert k > 1
    assert abs(res["bonferroni_threshold"] - floor.ALPHA / k) < 1e-9, (
        "the bar is not adjusted for the number of floors scanned — reporting "
        "the best of K comparisons at an unadjusted alpha is selection over an "
        "unstated denominator")


def test_a_tiny_p_never_prints_as_zero():
    """`p = 0.0` claims impossibility; it is also how a rounding bug hides.

    Rounding to 5dp made distinct p-values collide at 0.0, which created
    artificial ties and moved the reported floor from 2.0 to 0.41 — a wrong
    RECOMMENDATION out of a formatting choice, not a cosmetic issue.
    """
    res = floor.analyse(_separating(), "base_rate_IS")
    assert res["best_floor_by_p"]["p_one_sided"] > 0.0
    assert all(g["p_one_sided"] > 0.0 for g in res["grid"])


def test_tied_floors_are_disclosed():
    """Two floors reaching the SAME best p must both be named, not one silently picked.

    A tie is not hypothetical. These 20 rows (found by search over random
    corpora, then frozen so the case is deterministic) put floors 1.69 and 2.14
    at an identical p=0.0349 out of 10 tried. `min()` would return the first and
    a reader would take it as *the* floor the data selected, when the data does
    not distinguish the two at all — false precision on the number the whole
    analysis exists to produce.
    """
    rates = [1.54, 1.65, 2.86, 0.61, 2.32, 2.94, 2.15, 1.65, 1.2, 1.69,
             1.98, 2.46, 2.27, 0.76, 1.2, 2.97, 1.52, 2.14, 1.45, 2.87]
    wins = [1, 1, 5, 1, 5, 1, 5, 1, 1, 5, 1, 5, 1, 5, 1, 5, 1, 5, 5, 5]
    rows = [_cell(f"c{i}", r, w) for i, (r, w) in enumerate(zip(rates, wins))]
    res = floor.analyse(rows, "base_rate_IS")
    assert res["tied_floors"] == [1.69, 2.14], (
        f"expected both tied floors named, got {res.get('tied_floors')}")
    best_p = res["best_floor_by_p"]["p_one_sided"]
    assert all(g["p_one_sided"] == best_p for g in res["grid"]
               if g["floor"] in res["tied_floors"])


def test_the_reported_floor_is_always_one_of_the_tied_set():
    """Invariant: the number handed to the operator is a member of the tie."""
    for rows in (_separating(), [_cell(f"n{i}", 0.3 + 0.1 * i, 5 if i % 2 else 1)
                                 for i in range(24)]):
        res = floor.analyse(rows, "base_rate_IS")
        if res.get("tied_floors"):
            assert res["best_floor_by_p"]["floor"] in res["tied_floors"]


def test_fisher_matches_the_textbook_value():
    """[[3,1],[1,3]] one-sided is exactly 17/70 — an independent anchor."""
    assert abs(floor.fisher_exact_greater(3, 1, 1, 3) - 17 / 70) < 1e-12
    assert abs(floor.fisher_exact_greater(4, 0, 0, 4) - 1 / 70) < 1e-12


def test_the_population_block_reconciles_by_arithmetic():
    """Every exclusion is counted, so `analysed` is reachable from the corpus size.

    A population summary whose parts do not sum is how a silent drop hides.
    """
    rows = (_separating()
            + [_cell("nw", 0.4, None, None, ran=False)]
            + [{"kind": "cell", "leg": "m", "cell": "x", "wf_ran": True,
                "wf_wins": 5, "wf_usable": 6}]
            + [{"kind": "leg_status", "leg": "dead", "leg_status": "harness_error"}])
    p = floor.analyse(rows, "base_rate_IS")["population"]
    assert p["cells"] == p["cells_no_walkforward"] + p["cells_walkforwarded"]
    assert p["cells_walkforwarded"] == p["analysed"] + p["cells_missing_axis"]
    assert p["corpus_rows"] == p["cells"] + p["leg_status_rows"]


# ------------------------------------------------------------ 5. the extractor

def _verdicts_doc():
    return {"generated_at": "2026-08-10T22:00:00+00:00", "split": "2025-07-01",
            "tp_cap_pct": 0.099,
            "skipped": [{"leg": "skipped_leg", "reason": "data_missing"}],
            "verdicts": {
                "broken_leg": {"status": "harness_error", "error": "boom"},
                "good_leg": {
                    "proxy": False,
                    "base_book": {
                        "IS": {"net_total_r": 6.62, "max_drawdown_r": 16.41,
                               "net_r_per_drawdown_r": 0.4034,
                               "rate_ungradeable_why": None, "total_trades": 90},
                        "OOS": {"net_total_r": -2.0, "max_drawdown_r": 5.0,
                                "net_r_per_drawdown_r": None,
                                "rate_ungradeable_why": "base_unprofitable"}},
                    "selection": {"cells_tried": 11, "cells_withheld_inert": 2},
                    "levers": {"vol_trail": [{
                        "cell": "vt_cold10_t2.5", "verdict": "path_b_wf_pass",
                        "path_b_candidate": True, "walkforward": "4/6",
                        "gate": {"IS": {"d_net_r": 2.48, "d_max_dd": 1.59,
                                        "passed": False, "reason": "maxdd_worse"},
                                 "OOS": {"d_net_r": 1.0, "d_max_dd": 0.5,
                                         "passed": False, "reason": "maxdd_worse"}},
                        "capital": {"IS": {"d_net_r_per_capital_day": 0.008},
                                    "OOS": {"d_net_r_per_capital_day": 0.004}},
                        "dd_exchange_rate": {
                            "IS": {"headroom": 4.5576, "passes": True},
                            "OOS": {"headroom": None, "reason": "base_unprofitable"}},
                    }]}}}}


def test_extractor_keeps_legs_that_produced_nothing():
    """A leg that errored or was skipped is part of the fleet denominator."""
    rows = extract.rows_from_verdicts(_verdicts_doc(), "run1")
    statuses = {r["leg"]: r.get("leg_status") for r in rows
                if r["kind"] == "leg_status"}
    assert statuses == {"skipped_leg": "skipped", "broken_leg": "harness_error"}


def test_extractor_preserves_the_ungradeable_reason_not_a_zero():
    rows = extract.rows_from_verdicts(_verdicts_doc(), "run1")
    cell = next(r for r in rows if r["kind"] == "cell")
    assert cell["base_rate_IS"] == 0.4034
    assert cell["base_rate_OOS"] is None, "an unprofitable base book is not rate 0"
    assert cell["base_rate_ungradeable_why_OOS"] == "base_unprofitable"


def test_extractor_marks_a_run_that_predates_the_base_book():
    """An OLD corpus and an UNGRADEABLE book must not look the same.

    Only one of them is evidence about the leg; the other is evidence about the
    sweep's vintage.
    """
    doc = _verdicts_doc()
    del doc["verdicts"]["good_leg"]["base_book"]
    cell = next(r for r in extract.rows_from_verdicts(doc, "old")
                if r["kind"] == "cell")
    assert cell["base_book_present"] is False
    assert cell["base_rate_ungradeable_why_IS"] == "no_base_book_in_run"


def test_a_re_sweep_supersedes_by_measurement_not_by_run(tmp_path):
    """Re-measuring the same cell must REPLACE, not append.

    This is the defect that would corrupt the floor analysis invisibly. Keying
    the merge on the run id is the obvious choice: re-sweeping the same legs
    mints a NEW run id, both copies survive, and the population doubles without
    gaining one bit of information. Tonight's 4th and 5th dispatches are the
    worked case — byte-identical numbers, two run ids. Nothing about the corpus
    LOOKS wrong afterwards; only the count is, and the count is exactly what a
    floor's significance rests on.
    """
    corpus = tmp_path / "c.jsonl"
    first, second = tmp_path / "a", tmp_path / "b"
    for d, stamp in ((first, "2026-08-10T22:10:00+00:00"),
                     (second, "2026-08-10T22:40:00+00:00")):
        run = d / "leg" / "2026-08-10"
        run.mkdir(parents=True)
        doc = _verdicts_doc()
        doc["generated_at"] = stamp          # a genuinely different RUN...
        (run / "verdicts.json").write_text(json.dumps(doc))  # ...same MEASUREMENT

    assert extract.main(["x", "--in", str(first), "--corpus", str(corpus)]) == 0
    n_first = len([ln for ln in corpus.read_text().splitlines() if ln.strip()])
    assert extract.main(["x", "--in", str(second), "--corpus", str(corpus)]) == 0
    n_second = len([ln for ln in corpus.read_text().splitlines() if ln.strip()])

    assert n_second == n_first, (
        f"a re-sweep of the same legs grew the corpus {n_first} -> {n_second}; "
        "the same measurement is now counted twice and any downstream "
        "denominator is inflated")
    rows = [json.loads(ln) for ln in corpus.read_text().splitlines() if ln.strip()]
    assert all(r["sweep_generated_at"] == "2026-08-10T22:40:00+00:00" for r in rows), (
        "the NEWER measurement must win")


def test_the_same_cell_at_two_geometries_is_two_measurements(tmp_path):
    """Live-parity and legacy no-TP are different books, not a re-measurement.

    Collapsing them would re-commit BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP
    one level up — a corpus silently mixing a book that cannot take profit with
    one that does.
    """
    corpus = tmp_path / "c.jsonl"
    for i, cap in enumerate((0.099, 0.0)):
        run = tmp_path / f"g{i}" / "leg" / "2026-08-10"
        run.mkdir(parents=True)
        doc = _verdicts_doc()
        doc["tp_cap_pct"] = cap
        doc["generated_at"] = f"2026-08-10T2{i}:00:00+00:00"
        (run / "verdicts.json").write_text(json.dumps(doc))
        assert extract.main(["x", "--in", str(tmp_path / f"g{i}"),
                             "--corpus", str(corpus)]) == 0
    rows = [json.loads(ln) for ln in corpus.read_text().splitlines() if ln.strip()]
    caps = {r.get("tp_cap_pct") for r in rows if r["kind"] == "cell"}
    assert caps == {0.099, 0.0}, (
        f"both geometries must survive as separate measurements, got {caps}")


def test_a_run_predating_the_geometry_field_keys_distinctly(tmp_path):
    """`tp_cap_pct: null` is "we do not know which book", not the current one."""
    doc = _verdicts_doc()
    doc.pop("tp_cap_pct", None)
    row = next(r for r in extract.rows_from_verdicts(doc, "old") if r["kind"] == "cell")
    assert row["tp_cap_pct"] is None
    modern = dict(row, tp_cap_pct=0.099)
    assert extract.measurement_key(row) != extract.measurement_key(modern)


def test_reextracting_a_run_supersedes_it_rather_than_duplicating(tmp_path):
    corpus = tmp_path / "c.jsonl"
    src = tmp_path / "out" / "leg" / "2026-08-10"
    src.mkdir(parents=True)
    (src / "verdicts.json").write_text(json.dumps(_verdicts_doc()))
    argv = ["x", "--in", str(tmp_path / "out"), "--corpus", str(corpus)]
    assert extract.main(argv) == 0
    first = corpus.read_text().count("\n")
    assert extract.main(argv) == 0, "second extraction failed"
    assert corpus.read_text().count("\n") == first, (
        "re-extracting the same run duplicated its rows — a corpus that grows on "
        "re-runs inflates its own denominator")


def test_an_empty_extraction_fails_rather_than_reporting_success(tmp_path):
    """No verdicts.json is a FAILED extraction, not an empty sweep.

    Exiting 0 here would commit an unchanged corpus and read as 'the sweep added
    nothing' — sub-class C, an unasserted denominator read as a clean negative.
    """
    (tmp_path / "empty").mkdir()
    assert extract.main(["x", "--in", str(tmp_path / "empty"),
                         "--corpus", str(tmp_path / "c.jsonl")]) == 1
