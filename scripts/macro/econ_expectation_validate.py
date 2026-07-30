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
agreement**, and an OLS slope (model on survey). Correlation alone is not enough — a scale
error is invisible to it but shows up in the slope, and the units bug this pipeline already
hit (persons vs thousands, index level vs YoY percent) was exactly a scale error.

THE JOIN IS TOLERANT ON PURPOSE
-------------------------------
Keyless FRED dates observations by REFERENCE PERIOD, so the backfill emits a *modeled* release
date (`release_date_basis: modeled_lag`). A fixed lag lands exactly for a fixed-weekday series
(weekly claims/EIA) but the BLS CPI release drifts ~10th-15th, so exact-date equality silently
drops months (`BL-20260730-MONTHLY-RELEASE-DATE-DRIFT`). This matches within
``--tolerance-days`` and **reports the offset distribution**, so a systematic bias is visible
rather than absorbed into "small sample".

HONEST SMALL-n
--------------
The gate text assumes ~6 months of captured consensus; the real capture window is ~3 months,
which yields roughly a dozen joinable rows. Below ``--min-honest-n`` the verdict is
``insufficient_overlap`` — NOT a pass and NOT a fail. Asserting the kill condition at n=11
would be the same false confidence this pipeline keeps producing.

Observe-only, stdlib-only, Tier-1. Reads two committed JSONL files, writes a scorecard.

Usage::

    python scripts/macro/econ_expectation_validate.py \\
        --survey    comms/macro/econ_calendar_snapshots.jsonl \\
        --model     comms/macro/econ_calendar_snapshots_backfill.jsonl \\
        --json      comms/macro/econ_expectation_validation.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
from typing import Optional

SPEC_VERSION = "m3_overlap_validation_v1"
DEFAULT_SURVEY = os.path.join("comms", "macro", "econ_calendar_snapshots.jsonl")
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

    per_kind: dict[str, dict] = {}
    for p in pairs:
        per_kind.setdefault(p["kind"], {"n": 0, "sum_abs_gap": 0.0})
        per_kind[p["kind"]]["n"] += 1
        per_kind[p["kind"]]["sum_abs_gap"] += abs(p["model_surprise"] - p["survey_surprise"])
    for k, v in per_kind.items():
        v["mean_abs_gap"] = v["sum_abs_gap"] / v["n"] if v["n"] else None
        v.pop("sum_abs_gap")

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
            "condition cannot be asserted either way at this n. The captured-consensus window "
            "is ~3 months, while the gate text assumed ~6.")
    else:
        tracks = (sp is not None and sp >= BAR_SPEARMAN
                  and sg is not None and sg >= BAR_SIGN_AGREEMENT)
        verdict = "model_tracks_survey" if tracks else "model_does_not_track_survey"
        note = ("Pre-registered bar: Spearman >= %.2f AND sign agreement >= %.2f. A miss is the "
                "gate's kill condition -- option (b) is not a sound stand-in; fall back to (a) "
                "and re-scope the source." % (BAR_SPEARMAN, BAR_SIGN_AGREEMENT))

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
        "offset_days_min": min(offsets) if offsets else None,
        "offset_days_max": max(offsets) if offsets else None,
        "offset_days_mean": (sum(offsets) / len(offsets)) if offsets else None,
        "per_kind": per_kind,
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
        "— a scale error hides from correlation but shows here",
        f"- match offset days: min {report['offset_days_min']} / "
        f"mean {f(report['offset_days_mean'], 2)} / max {report['offset_days_max']}",
        "", "## Per kind", "",
        "| kind | n | mean abs gap (model−survey surprise) |", "|---|--:|--:|",
    ]
    for k, v in sorted(report["per_kind"].items()):
        lines.append(f"| {k} | {v['n']} | {f(v.get('mean_abs_gap'))} |")
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
    ap.add_argument("--survey", default=DEFAULT_SURVEY)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--json", default=DEFAULT_OUT)
    ap.add_argument("--tolerance-days", type=int, default=DEFAULT_TOLERANCE_DAYS)
    ap.add_argument("--min-honest-n", type=int, default=MIN_HONEST_N)
    ap.add_argument("--kinds", default=None, help="CSV subset of event kinds")
    ap.add_argument("--generated-at", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print; write nothing")
    args = ap.parse_args(argv)

    survey, model = read_rows(args.survey), read_rows(args.model)
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
    report["survey_path"] = args.survey
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
