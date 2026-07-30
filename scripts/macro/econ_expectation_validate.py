#!/usr/bin/env python3
"""M3 — validate the PIT expectation MODEL against captured SURVEY consensus on the overlap.

WHY THIS IS THE GATE, NOT A FOLLOW-UP
-------------------------------------
The operator-approved M1 gate (2026-07-30, ROADMAP_MACRO) accepts a surprise measured against
either (a) archived published consensus, or (b) a **pre-registered PIT expectation model**
*"validated against the ~6 months of captured survey consensus on the overlap"*. There is no
archived consensus history available, so M1 rests on (b) — and (b) is only satisfiable once
this validation exists and passes. Its kill condition is explicit: if model-surprise does not
track survey-surprise where both exist, the model is not a sound stand-in.

WHAT IT MEASURES
----------------
For each release present in BOTH sources:

    survey_surprise = actual - survey_consensus     (the market's own yardstick)
    model_surprise  = actual - model_expectation    (our stand-in)

and reports how well the second tracks the first: Spearman + Pearson correlation, **sign
agreement**, an OLS slope (model on survey), a **dispersion ratio**, and the **RMSE of each
surprise**. Correlation alone is not enough — the units bug this pipeline already hit (persons
vs thousands, index level vs YoY percent) was a pure scale error, invisible to correlation.

But the OLS slope is NOT the scale diagnostic it was originally described as here. Since
``slope = pearson × sd(model)/sd(survey)``, it conflates correlation with scale, and reading a
slope below 1 as "the model's surprises are smaller" produces a wrong diagnosis. That happened
on the first real run: continuing claims' slope of 0.523 was written up as a scale error when
``sd(model)=0.426`` vs ``sd(survey)=0.313`` — the model surprises are MORE dispersed and the low
slope is weak correlation (pearson ≈ 0.38). So:

* ``dispersion_ratio_model_over_survey`` — scale ONLY, correlation factored out.
* ``rmse_model`` / ``rmse_survey`` — accuracy. ``surprise = actual − expectation``, so the RMSE
  of a surprise series IS that expectation's error against what happened. This asks the question
  the correlation bars never do: *which expectation was closer to the outcome?* On the first real
  run the model was WORSE than the survey on all three kinds (1.30× / 1.36× / 2.60×) — a fact the
  pooled pass concealed entirely, and the one that most limits what option (b) can claim.

THE JOIN IS TOLERANT ON PURPOSE
-------------------------------
Keyless FRED dates observations by REFERENCE PERIOD, so the backfill emits a *modeled* release
date (`release_date_basis: modeled_lag`). A fixed lag lands exactly for a fixed-weekday series
(weekly claims/EIA) but the BLS CPI release drifts ~10th-15th, so exact-date equality silently
drops months (`BL-20260730-MONTHLY-RELEASE-DATE-DRIFT`). This matches within
``--tolerance-days`` and **reports the offset distribution**, so a systematic bias is visible
rather than absorbed into "small sample".

HONEST SMALL-n — AND WHY THE SMALL n WASN'T REAL
------------------------------------------------
Below ``--min-honest-n`` the verdict is ``insufficient_overlap`` — NOT a pass and NOT a
fail. Asserting a kill condition at n=11 would be the same false confidence this pipeline
keeps producing, so the floor is never lowered to manufacture a verdict.

The floor is also not the thing to work around. This module first ran at n=11 and its
docstring explained that as a data limit ("the real capture window is ~3 months"). That
was **wrong**: the survey side was thin because the forward producer had only ever pulled
ONE window, and FXStreet's calendar API takes an arbitrary range. Backfilling it took the
overlap to **1,263** (12,076 survey rows) — 115× the supposed ceiling. The lesson worth
keeping: when a sample size blocks a verdict, ask what BOUNDS it before scheduling around
it. "Wait for accrual" was the wrong answer twice in one day, on both sides of this join.

Observe-only, stdlib-only, Tier-1. Reads committed JSONL files, writes a scorecard.

Usage::

    python scripts/macro/econ_expectation_validate.py \\
        --survey    comms/macro/econ_calendar_snapshots.jsonl \\
                    comms/macro/econ_calendar_snapshots_survey_backfill.jsonl \\
        --model     comms/macro/econ_calendar_snapshots_backfill.jsonl \\
        --json      comms/macro/econ_expectation_validation.json

(Both survey paths are the DEFAULT — pass ``--survey`` only to narrow.)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
from typing import Optional

SPEC_VERSION = "m3_overlap_validation_v1"
# The survey side has TWO sources and BOTH must be read, or the tool measures a
# fraction of the available overlap and reports a verdict on it:
#   * the forward producer's file — one window, ever (~11 joinable rows), and
#   * the FXStreet history backfill — 12,076 rows, 1,263 joinable.
# Wiring only the first is how this tool reported `insufficient_overlap` at n=11 on
# 2026-07-30 while 1,263 joinable rows sat committed beside it: the survey-backfill
# WORKFLOW proved the 1,263 by passing `fwd + survey` to join_overlap by hand, but the
# tool's own defaults never grew the second path. Proving a join in the runner is not
# the same as wiring it into the thing that reads out the verdict.
DEFAULT_SURVEY = [
    os.path.join("comms", "macro", "econ_calendar_snapshots.jsonl"),
    os.path.join("comms", "macro", "econ_calendar_snapshots_survey_backfill.jsonl"),
]
DEFAULT_MODEL = os.path.join("comms", "macro", "econ_calendar_snapshots_backfill.jsonl")
DEFAULT_OUT = os.path.join("comms", "macro", "econ_expectation_validation.json")

# Below this the verdict is `insufficient_overlap` — neither pass nor fail.
MIN_HONEST_N = 12
# Pre-registered bar for "the model tracks the survey" (stated before the first run).
BAR_SPEARMAN = 0.5
BAR_SIGN_AGREEMENT = 0.7
DEFAULT_TOLERANCE_DAYS = 5


# --------------------------------------------------------------------------- io
def read_rows(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except FileNotFoundError:
        return []
    return out


def _nested(row: dict, *path):
    cur = row
    for key in path:
        cur = (cur or {}).get(key)
    return cur


def consensus_of(row: dict) -> Optional[float]:
    """The row's expectation, wherever it lives.

    Both files share a schema by design (so the study can filter on `kind` uniformly);
    provenance is what separates a survey poll from a model output — see `is_model_row`.
    """
    for path in (("realized_outcome", "consensus"), ("expected", "consensus")):
        v = _nested(row, *path)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def actual_of(row: dict) -> Optional[float]:
    v = _nested(row, "realized_outcome", "actual")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def is_model_row(row: dict) -> bool:
    """A MODEL expectation, never a survey poll — keyed on `expectation_source` alone.

    An earlier version also treated any `backfilled: true` row as a model row. That was a
    shortcut that held only while the model side was the ONLY thing ever backfilled, and it
    breaks the moment a SURVEY backfill exists: a retro-fetched real survey consensus is
    legitimately `backfilled: true`, so it would have been misclassified as a model row and
    silently dropped from the survey side of the comparison — leaving M3 quietly comparing the
    model against itself on those rows, or against nothing.

    `backfilled` answers "was this row reconstructed?"; `expectation_source` answers "where did
    the expectation come from?". Only the second is the axis this function is about.
    """
    return str(row.get("expectation_source") or "").startswith("model:")


# ------------------------------------------------------------------- statistics
def _rank(xs: list[float]) -> list[float]:
    """Average ranks, so ties don't bias the correlation."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 3:
        return None
    return pearson(_rank(xs), _rank(ys))


def _rmse(vals: list[float]) -> Optional[float]:
    """Root-mean-square of a surprise series.

    `surprise = actual - expectation`, so this IS the expectation's own error against what
    happened. It is the accuracy question the correlation bars never ask: two expectations can
    correlate strongly while one is systematically further from the outcome.
    """
    if not vals:
        return None
    return math.sqrt(sum(v * v for v in vals) / len(vals))


def _dispersion_ratio(ys: list[float], xs: list[float]) -> Optional[float]:
    """sd(ys)/sd(xs) — the SCALE comparison, with correlation factored out.

    Use this, not the OLS slope, to ask "are the model's surprises bigger or smaller than the
    survey's?". slope = pearson x this, so a slope below 1 can mean weak correlation, smaller
    scale, or both, and reading it as scale alone produces a wrong diagnosis (it did).
    """
    if len(ys) < 2 or len(xs) < 2:
        return None

    def _sd(v):
        m = sum(v) / len(v)
        return math.sqrt(sum((q - m) ** 2 for q in v) / len(v))

    sx = _sd(xs)
    return (_sd(ys) / sx) if sx else None


def ols_slope(xs: list[float], ys: list[float]) -> Optional[float]:
    """Slope of ys on xs. A units/scale error is invisible to correlation but shows here."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def sign_agreement(xs: list[float], ys: list[float]) -> Optional[float]:
    """Fraction of releases where both surprises point the same way.

    Rows where EITHER surprise is exactly zero are excluded: a zero has no direction, and
    counting it either way would manufacture agreement or disagreement out of nothing.
    """
    pairs = [(x, y) for x, y in zip(xs, ys) if x != 0 and y != 0]
    if not pairs:
        return None
    return sum(1 for x, y in pairs if (x > 0) == (y > 0)) / len(pairs)


# ------------------------------------------------------------------------- join
def _naive_block(pairs: list[dict]) -> dict:
    """RMSE of the naive random-walk expectation on these pairs + the beats-naive verdict.

    Only pairs that HAVE a prior release contribute, and the covered count is reported, so a
    thin naive sample can never masquerade as a clean floor test.
    """
    have = [p for p in pairs
            if isinstance(p.get("naive_expectation"), (int, float))
            and not isinstance(p.get("naive_expectation"), bool)]
    if not have:
        return {"rmse_naive": None, "naive_pairs": 0, "model_beats_naive": None,
                "rmse_ratio_model_over_naive": None}
    r_naive = _rmse([p["actual"] - p["naive_expectation"] for p in have])
    r_model = _rmse([p["actual"] - p["model_expectation"] for p in have])
    ratio = (r_model / r_naive) if r_naive else None
    return {
        "rmse_naive": r_naive,
        "naive_pairs": len(have),
        "rmse_ratio_model_over_naive": ratio,
        # < 1 means the model is closer to the outcome than "assume no change".
        "model_beats_naive": (ratio is not None and ratio < 1.0),
    }


def _prev_actual(row: dict, by_kind: dict, pos: dict) -> Optional[float]:
    """The PREVIOUS release's actual for this row's kind — the naive random-walk expectation.

    "Assume no change since last release", formed from the same information set the model had.
    It is the floor any expectation model must clear to be adding information at all, and the
    correlation bars never test it: on the first real run the model LOST to this floor on
    `initial_jobless_claims` (227.6 vs 206.8) — the very kind M3 graded as the STRONGEST
    tracker. A model can pass "tracks the survey" while being worse than doing nothing.
    """
    at = pos.get(id(row))
    if at is None:
        return None
    kind_key, idx = at
    if idx < 1:
        return None  # no prior release in the series; never fabricate one
    return actual_of(by_kind[kind_key][idx - 1][1])


def join_overlap(survey: list[dict], model: list[dict], *,
                 tolerance_days: int = DEFAULT_TOLERANCE_DAYS) -> list[dict]:
    """Pair survey rows with model rows on (kind, date within tolerance).

    Nearest match wins; each model row is consumed at most once so one model expectation
    cannot be double-counted against two survey rows.
    """
    by_kind: dict[str, list[tuple[_dt.date, dict]]] = {}
    for r in model:
        if not is_model_row(r):
            continue
        d = r.get("scheduled_for")
        if not d:
            continue
        try:
            by_kind.setdefault(r.get("kind"), []).append((_dt.date.fromisoformat(d), r))
        except ValueError:
            continue
    for v in by_kind.values():
        v.sort(key=lambda t: t[0])
    # Position index so each matched model row can reach its own PREVIOUS release. That
    # previous actual is the NAIVE RANDOM-WALK expectation ("assume no change"), formed from
    # exactly the same information set the model had — so it is a fair floor, and any model
    # that cannot clear it is not adding information.
    pos: dict[int, tuple[str, int]] = {}
    for kind_key, v in by_kind.items():
        for idx, (_d, row) in enumerate(v):
            pos[id(row)] = (kind_key, idx)

    used: set[int] = set()
    out: list[dict] = []
    for s in survey:
        if is_model_row(s):
            continue  # never validate the model against itself
        kind, sd = s.get("kind"), s.get("scheduled_for")
        sc, sa = consensus_of(s), actual_of(s)
        if not kind or not sd or sc is None or sa is None:
            continue
        try:
            sdate = _dt.date.fromisoformat(sd)
        except ValueError:
            continue
        best = None
        for mdate, m in by_kind.get(kind, []):
            off = (mdate - sdate).days
            if abs(off) > tolerance_days or id(m) in used:
                continue
            if best is None or abs(off) < abs(best[0]):
                best = (off, m)
        if best is None:
            continue
        off, m = best
        mc = consensus_of(m)
        if mc is None:
            continue
        used.add(id(m))
        out.append({
            "kind": kind,
            "survey_date": sd,
            "model_date": m.get("scheduled_for"),
            "reference_period": m.get("reference_period"),
            "offset_days": off,
            "actual": sa,
            "survey_consensus": sc,
            "model_expectation": mc,
            "survey_surprise": sa - sc,
            "model_surprise": sa - mc,
            "units_transform": m.get("units_transform"),
            "release_date_basis": m.get("release_date_basis"),
            "naive_expectation": _prev_actual(m, by_kind, pos),
        })
    return out


def provenance_problems(pairs: list[dict]) -> list[str]:
    """Refuse to be read when the model side predates the units/release-date fixes.

    THE FIRST RUN OF THIS TOOL PROVED WHY THIS IS NEEDED. Against the then-committed
    backfill it produced Spearman **-0.5982** and an OLS slope of **-49205** — which reads
    as "the model anti-correlates with the survey", i.e. the gate's kill condition. It was
    nothing of the kind: that file predated the transform + release-lag fixes, so raw FRED
    units (persons vs millions) were being differenced against survey units and the dates
    were reference periods. A plausible, decision-shaped, completely wrong number.

    So the tool checks its own inputs' provenance instead of trusting them
    (docs/CLAUDE-RULES-CANONICAL.md "Green is not evidence", obligation 1).
    """
    problems = []
    missing_units = sum(1 for p in pairs if not p.get("units_transform"))
    missing_basis = sum(1 for p in pairs if not p.get("release_date_basis"))
    if missing_units:
        problems.append(
            f"{missing_units}/{len(pairs)} model rows carry no `units_transform` — the file "
            "predates the unit-transform fix, so surprises are being compared across "
            "DIFFERENT UNITS. Regenerate the backfill before reading any correlation.")
    if missing_basis:
        problems.append(
            f"{missing_basis}/{len(pairs)} model rows carry no `release_date_basis` — the "
            "file predates the release-date fix, so rows are keyed by REFERENCE PERIOD, not "
            "release. Regenerate the backfill.")
    return problems


# --------------------------------------------------------------------- scoring
def score(pairs: list[dict], *, min_honest_n: int = MIN_HONEST_N) -> dict:
    xs = [p["survey_surprise"] for p in pairs]
    ys = [p["model_surprise"] for p in pairs]
    sp, pe = spearman(xs, ys), pearson(xs, ys)
    sl, sg = ols_slope(xs, ys), sign_agreement(xs, ys)
    offsets = [p["offset_days"] for p in pairs]

    # PER-KIND ON THE SAME AXES AS THE VERDICT, not just a gap column.
    # The pooled number is an average over kinds that do NOT behave alike: at n=1263 the
    # pooled Spearman was 0.5885 while the per-kind spread was wide, and `initial_jobless_claims`
    # carried a mean abs gap of 26.6 against 0.11 / 0.24 for the other two. A pooled pass can
    # therefore be carried by two kinds while a third fails — and it is the PER-KIND answer that
    # decides whether the model is a sound stand-in for the kind you are about to trade. Same
    # population-match discipline the rigor standard requires of every other harness here:
    # measure the thing you will act on, not a blend that contains it.
    by_kind_pairs: dict[str, list[dict]] = {}
    for p in pairs:
        by_kind_pairs.setdefault(p["kind"], []).append(p)

    # ⚠️ `mean_abs_gap` CANNOT RANK KINDS — it is in each kind's own units. Claims surprises
    # are in thousands-of-persons, CPI in percentage points, so a gap of 26.6 and a gap of 0.11
    # are not comparable numbers. On the first n=1263 run this trap fired immediately: the 26.6
    # gap on `initial_jobless_claims` was read as "the weak kind", when it is in fact the
    # STRONGEST tracker (Spearman 0.63 / sign 0.79), while `continuing_jobless_claims` — the
    # SMALLEST gap at 0.11 — is the one that misses the bars (0.42 / 0.64, slope 0.52). Use the
    # scale-free columns (Spearman, sign agreement) to compare kinds; read mean_abs_gap only
    # within a kind, and the slope for a scale error inside one.
    per_kind: dict[str, dict] = {}
    for k, kp in by_kind_pairs.items():
        kxs = [q["survey_surprise"] for q in kp]
        kys = [q["model_surprise"] for q in kp]
        ksp, ksg = spearman(kxs, kys), sign_agreement(kxs, kys)
        enough = len(kp) >= min_honest_n
        per_kind[k] = {
            "n": len(kp),
            "mean_abs_gap": sum(abs(a - b) for a, b in zip(kys, kxs)) / len(kp),
            "spearman": ksp,
            "sign_agreement": ksg,
            "ols_slope_model_on_survey": ols_slope(kxs, kys),
            # THE SLOPE IS NOT A SCALE DIAGNOSTIC. slope = pearson x sd(model)/sd(survey), so
            # it conflates correlation with scale and a slope < 1 says nothing on its own about
            # magnitudes. This bit me on the first real run: continuing claims' slope of 0.523
            # was written up as "the model surprise is ~half the survey's — a scale error", when
            # sd(model)=0.426 vs sd(survey)=0.313 means the model surprise is MORE dispersed and
            # the low slope is weak correlation (pearson ~0.38). These two columns are the
            # separated versions — dispersion_ratio is scale ONLY, rmse_* is accuracy.
            "dispersion_ratio_model_over_survey": _dispersion_ratio(kys, kxs),
            # `surprise` is `actual - expectation`, so |surprise| IS the expectation's own error:
            # rmse_* answers "which expectation is closer to what happened?" — the question the
            # correlation bars never ask. On the first real run the model was WORSE than the
            # survey on all three kinds (1.30x / 1.36x / 2.60x), which the pooled pass hid.
            "rmse_model": _rmse(kys),
            "rmse_survey": _rmse(kxs),
            "rmse_ratio_model_over_survey": (
                _rmse(kys) / _rmse(kxs) if _rmse(kxs) else None),
            # THE FLOOR TEST. A model that cannot beat "assume no change since last release"
            # is not adding information, however well its surprises correlate with the
            # survey's. Reported alongside so a tracking pass can never imply usefulness.
            **_naive_block(kp),
            # Each kind is graded against the SAME pre-registered bars, and a kind below
            # min_honest_n gets `insufficient_overlap` rather than a flattering pass on
            # a handful of rows.
            "verdict": (
                "insufficient_overlap" if not enough
                else "model_tracks_survey"
                if (ksp is not None and ksp >= BAR_SPEARMAN
                    and ksg is not None and ksg >= BAR_SIGN_AGREEMENT)
                else "model_does_not_track_survey"),
        }

    n = len(pairs)
    stale = provenance_problems(pairs)
    if stale:
        # Pre-empts BOTH pass and fail: a correlation computed across mismatched units or
        # mis-keyed dates is not weak evidence, it is NO evidence.
        verdict, note = "stale_model_inputs", (
            "Model-side rows predate the units/release-date fixes, so this comparison is "
            "not interpretable in either direction. " + " ".join(stale))
    elif n < min_honest_n:
        verdict, note = "insufficient_overlap", (
            f"n={n} < min_honest_n={min_honest_n}. NOT a pass and NOT a fail: the gate's kill "
            "condition cannot be asserted either way at this n. Before scheduling around this, "
            "check what actually BOUNDS the overlap — a thin survey side was a scheduling "
            "artifact once already (one pulled window vs a range-based API), and backfilling it "
            "took n from 11 to 1263.")
    else:
        tracks = (sp is not None and sp >= BAR_SPEARMAN
                  and sg is not None and sg >= BAR_SIGN_AGREEMENT)
        verdict = "model_tracks_survey" if tracks else "model_does_not_track_survey"
        note = ("Pre-registered bar: Spearman >= %.2f AND sign agreement >= %.2f. A miss is the "
                "gate's kill condition -- option (b) is not a sound stand-in; fall back to (a) "
                "and re-scope the source." % (BAR_SPEARMAN, BAR_SIGN_AGREEMENT))

    # The pooled verdict stays EXACTLY the pre-registered rule — narrowing it after seeing the
    # data would be post-hoc bar-moving, the failure this module exists to avoid. But a pooled
    # pass that hides a failing kind must not read as "the model tracks the survey, full stop",
    # so the failing kinds ride alongside the verdict instead of being left in a table.
    failing = sorted(k for k, v in per_kind.items()
                     if v["verdict"] == "model_does_not_track_survey")
    # A SEPARATE axis from tracking, and it must ride next to the verdict: a kind whose model
    # is beaten by "assume no change since last release" is not adding information at all.
    worse_than_naive = sorted(k for k, v in per_kind.items()
                              if v.get("model_beats_naive") is False)
    thin = sorted(k for k, v in per_kind.items() if v["verdict"] == "insufficient_overlap")
    if worse_than_naive:
        note += (" ⚠️ WORSE THAN A NAIVE RANDOM WALK on: " + ", ".join(worse_than_naive)
                 + ". For these kinds the model expectation is FURTHER from the outcome than "
                   "'assume no change since last release', so it adds no information even where "
                   "it passes the tracking bars — tracking and usefulness are different axes and "
                   "this gate only tests the first.")
    if failing and verdict == "model_tracks_survey":
        note += (" ⚠️ POOLED PASS, PER-KIND MISS: " + ", ".join(failing) + " miss the same bars "
                 "individually. The pooled pass is carried by the other kinds — do NOT read it "
                 "as licence to use the model expectation for a kind in that list.")

    return {
        "spec_version": SPEC_VERSION,
        "n_overlap": n,
        "min_honest_n": min_honest_n,
        "bar_spearman": BAR_SPEARMAN,
        "bar_sign_agreement": BAR_SIGN_AGREEMENT,
        "spearman": sp,
        "pearson": pe,
        "ols_slope_model_on_survey": sl,
        "sign_agreement": sg,
        # Scale and accuracy, separated from correlation -- see `_dispersion_ratio` /
        # `_rmse`. The OLS slope alone is NOT a scale diagnostic.
        "dispersion_ratio_model_over_survey": _dispersion_ratio(ys, xs),
        "rmse_model": _rmse(ys),
        "rmse_survey": _rmse(xs),
        "rmse_ratio_model_over_survey": (_rmse(ys) / _rmse(xs)) if _rmse(xs) else None,
        "offset_days_min": min(offsets) if offsets else None,
        "offset_days_max": max(offsets) if offsets else None,
        "offset_days_mean": (sum(offsets) / len(offsets)) if offsets else None,
        "per_kind": per_kind,
        "kinds_not_tracking": failing,
        "kinds_worse_than_naive": worse_than_naive,
        "kinds_insufficient_overlap": thin,
        "verdict": verdict,
        "note": note,
        "provenance_problems": stale,
    }


def render(report: dict, pairs: list[dict]) -> str:
    def f(v, nd=4):
        return "—" if v is None else f"{v:.{nd}f}"
    lines = [
        "# M3 — expectation-model vs survey-consensus validation", "",
        f"- overlap rows: **{report['n_overlap']}** (min honest n {report['min_honest_n']})",
        f"- Spearman **{f(report['spearman'])}** · Pearson {f(report['pearson'])} "
        f"· sign agreement {f(report['sign_agreement'], 3)}",
        f"- OLS slope (model on survey) {f(report['ols_slope_model_on_survey'])} "
        f"= pearson × dispersion {f(report.get('dispersion_ratio_model_over_survey'), 3)} "
        "— the slope CONFLATES the two, so read the dispersion for scale",
        f"- expectation error (RMSE of the surprise): model {f(report.get('rmse_model'))} "
        f"vs survey {f(report.get('rmse_survey'))} "
        f"= **{f(report.get('rmse_ratio_model_over_survey'), 3)}×** "
        "— >1 means the model expectation is FURTHER from the outcome than the survey's, "
        "which the correlation bars do not test",
        f"- match offset days: min {report['offset_days_min']} / "
        f"mean {f(report['offset_days_mean'], 2)} / max {report['offset_days_max']}",
        "", "## Per kind — graded on the SAME pre-registered bars as the pooled verdict", "",
        "| kind | n | Spearman | sign agr. | slope | dispersion | RMSE mdl/svy "
        "| RMSE mdl/naive | verdict |",
        "|---|--:|--:|--:|--:|--:|--:|--:|---|",
    ]
    for k, v in sorted(report["per_kind"].items()):
        mark = {"model_tracks_survey": "✅ tracks",
                "model_does_not_track_survey": "❌ does NOT track",
                "insufficient_overlap": "— insufficient n"}.get(v["verdict"], v["verdict"])
        lines.append(
            f"| {k} | {v['n']} | {f(v.get('spearman'))} | {f(v.get('sign_agreement'), 3)} "
            f"| {f(v.get('ols_slope_model_on_survey'))} "
            f"| {f(v.get('dispersion_ratio_model_over_survey'), 3)} "
            f"| {f(v.get('rmse_ratio_model_over_survey'), 3)} "
            f"| {f(v.get('rmse_ratio_model_over_naive'), 3)}"
            f"{'' if v.get('model_beats_naive') is not False else ' ⚠️'} | {mark} |")
    if report.get("kinds_worse_than_naive"):
        lines += ["", "**⚠️ WORSE THAN A NAIVE RANDOM WALK:** "
                  + ", ".join(report["kinds_worse_than_naive"])
                  + " — the model expectation is further from the outcome than \"assume no "
                    "change since last release\". These kinds add NO information, including "
                    "where they pass the tracking bars above. Tracking and usefulness are "
                    "different axes; this gate only tests the first."]
    if report.get("kinds_not_tracking"):
        lines += ["", "**Per-kind misses:** " + ", ".join(report["kinds_not_tracking"])
                  + " — the model expectation is NOT a sound stand-in for these kinds, "
                    "whatever the pooled row says."]
    if report.get("provenance_problems"):
        lines += ["", "## ⚠️ INPUTS ARE STALE — the numbers above are NOT interpretable", ""]
        lines += [f"- {p}" for p in report["provenance_problems"]]
    lines += ["", f"## VERDICT — **{report['verdict']}**", "", f"> {report['note']}"]
    if pairs:
        b = pairs[0]
        lines += ["", "Provenance of the model side: "
                  f"`release_date_basis={b.get('release_date_basis')}`, "
                  f"`units_transform={b.get('units_transform')}`."]
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--survey", nargs="+", default=list(DEFAULT_SURVEY),
                    help="one or more survey-side JSONL files (forward producer + backfill)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--json", default=DEFAULT_OUT)
    ap.add_argument("--tolerance-days", type=int, default=DEFAULT_TOLERANCE_DAYS)
    ap.add_argument("--min-honest-n", type=int, default=MIN_HONEST_N)
    ap.add_argument("--kinds", default=None, help="CSV subset of event kinds")
    ap.add_argument("--generated-at", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print; write nothing")
    args = ap.parse_args(argv)

    survey_paths = [args.survey] if isinstance(args.survey, str) else list(args.survey)
    survey, per_path, missing = [], {}, []
    for p in survey_paths:
        rows = read_rows(p)
        per_path[p] = len(rows)
        # `read_rows` is FileNotFound-tolerant, which is right for an optional source but
        # would otherwise make "the file isn't there" and "the file is thin" produce the
        # same n. Record it so a small overlap can never be silently an absent input.
        if not os.path.exists(p):
            missing.append(p)
        survey.extend(rows)
    model = read_rows(args.model)
    if missing:
        print("::warning::survey source(s) absent, so the overlap below is measured "
              "WITHOUT them: " + ", ".join(missing))
    if not survey or not model:
        # Empty input is a hard failure, not an empty verdict: a scorecard computed from
        # nothing is vacuous, and vacuous is indistinguishable from thin once published.
        print(f"::error::no rows to validate (survey={len(survey)}, model={len(model)}) — "
              "the validation would measure nothing")
        return 2

    if args.kinds:
        want = {k.strip() for k in args.kinds.split(",") if k.strip()}
        survey = [r for r in survey if r.get("kind") in want]
        model = [r for r in model if r.get("kind") in want]

    pairs = join_overlap(survey, model, tolerance_days=args.tolerance_days)
    if not pairs:
        print("::error::the overlap join produced ZERO pairs — check the release-date basis "
              "and the tolerance window before reading anything into this")
        return 2

    report = score(pairs, min_honest_n=args.min_honest_n)
    report["generated_at"] = args.generated_at
    report["survey_paths"] = survey_paths
    report["survey_rows_by_path"] = per_path
    report["survey_paths_missing"] = missing
    report["model_path"] = args.model
    report["tolerance_days"] = args.tolerance_days
    print(render(report, pairs))

    if not args.dry_run:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"report": report, "pairs": pairs}, fh, indent=2)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
