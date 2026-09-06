#!/usr/bin/env python3
# wiring: manual-only — a research READ a session runs on the trainer, where the
# historical candle store lives. It trains nothing that ships, writes no config
# and has no runtime caller; the geometry it informs is Tier-3 and
# operator-gated, so a scheduled runner would be producing evidence for a
# decision nobody has taken yet. Dispatch it through the trainer-vm-diag relay
# (see docs/research/ml2-predictive-bracket-2026-09-06.md § 1).
"""ML-2 · train the predictive bracket and grade it — CALIBRATION FIRST.

E3.6's falsifier is the contract this script implements, and it is a falsifier
rather than a preference:

    *"a predictive bracket is a claim about WHERE the trade will exit, so it is
    graded against realised exits — calibration first (does the stated
    expectation match the observed distribution?), P&L second. A bracket that
    improves net R while being systematically wrong about where trades exit has
    NOT met this bar; it has found a different edge and should say so."*

So this script **prints the calibration block before the P&L block, always**,
and `--pnl` is opt-in. That ordering is not cosmetic: a P&L number read first
reframes every calibration number after it as an excuse.

--------------------------------------------------------------------------
WHAT IS ACTUALLY BEING TESTED — TWO BARS, NEITHER SUBSTITUTING
--------------------------------------------------------------------------
1. **Calibration** — does a predicted q-quantile get reached (1-q) of the time?
2. **Sharpness** — does it beat the UNCONDITIONAL quantile out of sample?

Bar 2 is the one that matters, because **bar 1 is satisfied by a model that
ignores every feature**. The unconditional empirical quantile is calibrated by
construction. So "ML-2 is calibrated" is, alone, a statement about arithmetic
rather than about the market.

If the conditional model is calibrated and NOT sharper, the honest conclusion
is that the per-leg MFE histogram is the whole answer and no model is needed —
which is what MI-148 already proposed. **Reporting that is a result.**

--------------------------------------------------------------------------
THE SPLIT IS CHRONOLOGICAL, AND THE CONTROL MUST BE SHOWN TO FIRE
--------------------------------------------------------------------------
* **Chronological split**, never random: a random split leaks the future into
  the training set through overlapping market regimes, and every quantile this
  fits would be optimistic.
* **Shuffled-label control** — E3.6 inherits E2's information test *"with a
  shuffled-label control THAT IS SHOWN TO FIRE"*. One draw cannot distinguish
  5% bad luck from a dead null (`e2_null_calibration.py` exists for exactly
  that), so `shuffled_label_control` returns the null DISTRIBUTION over K
  refits and the real improvement is compared against its upper tail.
* **Split dispersion (E4)** — the verdict is recomputed at several split
  fractions. `docs/design/exit-mechanism-construction-PROCESS.md` § 0.3
  measured that holding corpus and commit fixed and moving only the split
  50 -> 35 swung dOOS **5.14x** and flipped a pre-registered rule PASS -> FAIL.
  A verdict that moves with the split is `split_sensitive`, which § E4 calls a
  **refusal, not a caveat**.

Tier-1, observe-only. No config, no live target values, no order path, no
runtime caller. What it produces is evidence for a Tier-3 PROPOSAL.

Usage:
    python scripts/research/ml2_bracket_train_eval.py \
        --corpus /tmp/ml2_corpus.jsonl --out /tmp/ml2_eval.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "scripts" / "research") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts" / "research"))

from src.research.bracket_quantile import (  # noqa: E402
    DEFAULT_QUANTILES, MIN_EVAL_N, SHARP_BEATS_BASELINE,
    QuantileRegressor, calibration_curve, empirical_quantile,
    grade_model, mean_absolute_calibration_error, shuffled_label_control,
)
from ml2_bracket_corpus import FEATURE_NAMES, feature_matrix  # noqa: E402

#: Split fractions for the E4 dispersion test. 0.65 is the headline.
DISPERSION_SPLITS = (0.55, 0.65, 0.75)


def _read_corpus(path: str) -> Tuple[List[Dict[str, Any]], int]:
    """Return (rows, malformed_line_count) — the count is surfaced, not swallowed.

    A wholly unreadable corpus and an empty one both render as "0 rows"
    otherwise, and only one of those is a result.
    """
    rows: List[Dict[str, Any]] = []
    malformed = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                malformed += 1
    return rows, malformed


def _chrono_key(r: Dict[str, Any]) -> str:
    return str(r.get("entry_time") or "")


def evaluate(
    rows: Sequence[Dict[str, Any]],
    *,
    outcome: str = "mfe_frac",
    quantiles: Sequence[float] = DEFAULT_QUANTILES,
    split: float = 0.65,
    min_n: int = MIN_EVAL_N,
    control_trials: int = 20,
    seed: int = 0,
) -> Dict[str, Any]:
    """One full evaluation at ONE split. Returns the calibration block first."""
    ordered = sorted(rows, key=_chrono_key)
    X, y, dropped = feature_matrix(ordered, outcome=outcome)
    n = len(y)
    cut = int(n * split)
    Xtr, ytr, Xev, yev = X[:cut], y[:cut], X[cut:], y[cut:]

    out: Dict[str, Any] = {
        "outcome": outcome,
        "basis": "percent_of_entry",
        "split": split,
        "population": {
            "rows_in": len(rows),
            "usable": n,
            "dropped_missing_feature": dropped["missing_feature"],
            "dropped_missing_outcome": dropped["missing_outcome"],
            "n_train": len(ytr),
            "n_eval": len(yev),
            "min_eval_n": min_n,
            "features": list(FEATURE_NAMES),
        },
        "calibration": [],
        "mace": None,
        "sharpness": [],
        "control": None,
        "verdict": "not_measured",
    }
    if len(yev) < min_n or len(ytr) < min_n:
        # NOT a failure of the model — no held-out coverage figure EXISTS.
        out["verdict"] = "insufficient_n"
        return out

    preds_by_q: Dict[float, List[Optional[float]]] = {}
    controls: Dict[float, Dict[str, Any]] = {}
    for q in quantiles:
        m = QuantileRegressor(q, seed=seed).fit(Xtr, ytr)
        base = empirical_quantile(ytr, q)
        # ⚠️ NOT a probability, and this file prints one a column away.
        # `coverage` IS a probability (fraction of outcomes at or below the
        # level); this is a price DISTANCE. Reading the level as a probability
        # would report a 4.3%-of-entry target as "4% likely".
        # provenance: QuantileRegressor.predict — the conditional q-QUANTILE of
        # the outcome in PERCENT-OF-ENTRY (e.g. 0.043 = 4.3% of entry)
        mp = m.predict(Xev) if m.fitted else [None] * len(yev)
        bp: List[Optional[float]] = [base] * len(yev)
        preds_by_q[q] = mp
        # THE CONTROL IS RUN PER QUANTILE AND GATES THAT QUANTILE'S VERDICT.
        # It used to run once, at the middle q, and only be REPORTED -- so a
        # sub-null improvement still graded as a win. The null's width is not
        # constant in q (the pinball loss is asymmetric), so one null cannot
        # gate five quantiles even if it were wired in.
        ctrl = shuffled_label_control(Xtr, ytr, Xev, yev, q,
                                      trials=control_trials, seed=seed, min_n=min_n)
        controls[q] = ctrl
        out["sharpness"].append(
            grade_model(yev, mp, bp, q, min_n=min_n,
                        null_p95=ctrl.get("null_p95_improvement")))

    out["calibration"] = calibration_curve(yev, preds_by_q)  # type: ignore[arg-type]
    out["mace"] = mean_absolute_calibration_error(out["calibration"])
    out["control"] = {"per_quantile": controls,
                      "headline_q": quantiles[len(quantiles) // 2],
                      **controls[quantiles[len(quantiles) // 2]]}

    graded = [c for c in out["calibration"] if c.get("coverage") is not None]
    sharp = [s for s in out["sharpness"] if s["sharpness_state"] == SHARP_BEATS_BASELINE]
    if not graded:
        out["verdict"] = "not_measured"
    elif out["mace"] is not None and out["mace"] <= 0.05 and len(sharp) >= len(quantiles) / 2:
        out["verdict"] = "calibrated_and_sharper"
    elif out["mace"] is not None and out["mace"] <= 0.05:
        # The vacuous-calibration case. A RESULT, and the likeliest one.
        out["verdict"] = "calibrated_but_no_sharper_than_baseline"
    else:
        out["verdict"] = "miscalibrated"
    return out


def dispersion(rows: Sequence[Dict[str, Any]], *, headline_verdict: Optional[str] = None,
               **kw: Any) -> Dict[str, Any]:
    """E4: recompute the verdict at several splits. Disagreement is a REFUSAL.

    ⚠️ **THE ARMS MUST USE THE SAME CONTROL CONFIGURATION AS THE HEADLINE, AND
    ONCE DID NOT.** An earlier cut ran the arms at `control_trials=3` while the
    headline ran at 10. A null estimated from 3 draws is wider-tailed and
    noisier, so the arms cleared a *different* bar — and the smoke run showed
    exactly what that produces: every arm reading `calibrated_and_sharper` and
    `split_sensitive: False` beneath a headline of
    `calibrated_but_no_sharper_than_baseline`. A stability claim about a
    measurement other than the one reported is worse than no stability claim,
    because it reads as corroboration.

    Self-consistency is now ASSERTED rather than hoped for: the arm at the
    headline split must reproduce the headline verdict, and `arms_consistent`
    says whether it did. A mismatch means the two are not the same computation
    and neither the stability claim nor the headline should be trusted.
    """
    arms = []
    for sp in DISPERSION_SPLITS:
        kw2 = dict(kw)
        kw2["split"] = sp
        r = evaluate(rows, **kw2)
        arms.append({"split": sp, "verdict": r["verdict"], "mace": r["mace"],
                     "n_eval": r["population"]["n_eval"]})
    verdicts = {a["verdict"] for a in arms}
    at_headline = [a for a in arms if abs(a["split"] - 0.65) < 1e-9]
    consistent: Optional[bool] = None
    if headline_verdict is not None and at_headline:
        consistent = (at_headline[0]["verdict"] == headline_verdict)
    return {
        "arms": arms,
        "control_trials": kw.get("control_trials"),
        "split_sensitive": len(verdicts) > 1,
        "arms_consistent_with_headline": consistent,
        "note": ("§ E4: split_sensitive is a REFUSAL, not a caveat — the verdict "
                 "does not proceed." if len(verdicts) > 1 else
                 "verdict stable across splits"),
    }


def _fmt(v: Any, p: int = 4) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{p}f}"
    return str(v)


def render(result: Dict[str, Any], disp: Optional[Dict[str, Any]]) -> None:
    pop = result["population"]
    print("=" * 74)
    print("ML-2 PREDICTIVE BRACKET — CALIBRATION READ (published BEFORE any P&L)")
    print("=" * 74)
    print(f"outcome        : {result['outcome']}  (basis: {result['basis']})")
    print(f"POPULATION     : rows_in={pop['rows_in']}  usable={pop['usable']}  "
          f"train={pop['n_train']}  eval={pop['n_eval']}")
    print(f"                 dropped: missing_feature={pop['dropped_missing_feature']} "
          f"missing_outcome={pop['dropped_missing_outcome']}")
    print(f"features       : {', '.join(pop['features'])}")
    print()
    if result["verdict"] in ("insufficient_n", "not_measured"):
        print(f"VERDICT: {result['verdict']} — no held-out coverage figure EXISTS.")
        print("This is NOT 'calibrated' and NOT 'miscalibrated'.")
        return

    print("--- 1. CALIBRATION (does the stated quantile match observed coverage?) ---")
    print(f"{'target q':>9} {'coverage':>9} {'|err|':>8}")
    for c in result["calibration"]:
        print(f"{c['q']:>9.2f} {_fmt(c['coverage'],4):>9} {_fmt(c['coverage_error'],4):>8}")
    print(f"MACE (mean abs calibration error): {_fmt(result['mace'])}")
    print()
    print("--- 2. SHARPNESS (does it beat the UNCONDITIONAL quantile? the real bar) ---")
    print(f"{'q':>5} {'model_pin':>11} {'base_pin':>11} {'improve':>9} {'null_p95':>9}  state")
    for s in result["sharpness"]:
        print(f"{s['q']:>5.2f} {_fmt(s['model_pinball'],6):>11} "
              f"{_fmt(s['baseline_pinball'],6):>11} "
              f"{_fmt(s['pinball_improvement'],4):>9} "
              f"{_fmt(s.get('null_p95'),4):>9}  {s['sharpness_state']}")
    print()
    c = result.get("control") or {}
    print("--- 3. SHUFFLED-LABEL CONTROL (run PER QUANTILE; it GATES section 2) ---")
    for q, cc in sorted((c.get("per_quantile") or {}).items()):
        print(f"  q={q:.2f} state={cc.get('control_state')} "
              f"trials={cc.get('trials_usable')} "
              f"null_mean={_fmt(cc.get('null_mean_improvement'))} "
              f"null_p95={_fmt(cc.get('null_p95_improvement'))}")
    print("  An improvement inside the null's upper tail carries NO information —")
    print("  it grades `beats_baseline_within_null`, which is a refusal, not a pass.")
    print()
    if disp:
        print("--- 4. SPLIT DISPERSION (E4) ---")
        for a in disp["arms"]:
            print(f"  split={a['split']:.2f} n_eval={a['n_eval']:>5} "
                  f"mace={_fmt(a['mace'])} verdict={a['verdict']}")
        print(f"  split_sensitive={disp['split_sensitive']} — {disp['note']}")
        cons = disp.get("arms_consistent_with_headline")
        if cons is False:
            print("  ⚠️  ARM AT THE HEADLINE SPLIT DISAGREES WITH THE HEADLINE.")
            print("      These are then not the same computation, and neither the")
            print("      stability claim nor the headline verdict should be trusted.")
        elif cons is True:
            print("  arms_consistent_with_headline=True (same computation, asserted)")
        print()
    print("=" * 74)
    print(f"VERDICT: {result['verdict']}")
    if result["verdict"] == "calibrated_but_no_sharper_than_baseline":
        print("  Read this carefully: the model is calibrated AND adds nothing over")
        print("  the leg's own unconditional MFE quantile. The unconditional quantile")
        print("  is calibrated BY CONSTRUCTION, so this verdict means the features")
        print("  carry no information about WHERE THIS trade will exit. The honest")
        print("  conclusion is the per-leg MFE histogram MI-148 already proposed —")
        print("  no model required.")
    print("=" * 74)


def selftest(seeds: int = 6, n: int = 500, trials: int = 8) -> int:
    """Positive + negative control, as a RATE over seeds — never a single draw.

    ⚠️ **This function's first version graded ONE draw and it was wrong.** It
    reported the no-signal arm as `5/5 beats_baseline`, i.e. a full false
    positive, because that seed happened to land in the null's tail. Re-run
    across 6 seeds the same arm gave sharp counts [0, 1, 0, 2, 0, 1] of 5 —
    the estimator was fine and the TEST was not.

    That is precisely the failure `scripts/research/e2_null_calibration.py`
    exists to prevent, in its own words: *"E2 injects ONE negative control, so a
    single run yields a single Bernoulli draw and cannot tell 5% bad luck from a
    broken null."* A control is validated by its RATE.

    Asserted here, over `seeds` independent draws:
      * SIGNAL   — the majority rule fires on >= 80% of seeds (it must find a
                   signal that IS there);
      * NO-SIGNAL— it fires on <= 20% of seeds (the false-positive rate).
    """
    import random

    def _mk(nn: int, signal: bool, seed: int) -> List[Dict[str, Any]]:
        rng = random.Random(seed)
        rows = []
        for i in range(nn):
            rf = rng.uniform(0.005, 0.05)
            base = (3.0 * rf) if signal else 0.02
            mfe = max(0.0, base + rng.expovariate(1 / 0.01))
            rows.append({
                "leg": "synthetic", "symbol": "X",
                "entry_time": f"2026-01-{1 + i % 28:02d} {i % 24:02d}:00:00",
                "risk_frac": rf, "is_long": float(i % 2), "confidence": rng.random(),
                "hour_sin": math.sin(i), "hour_cos": math.cos(i), "dow": float(i % 7),
                "mfe_frac": mfe,
            })
        return rows

    # The FULL default quantile set, so the selftest validates the configuration
    # the real runs actually use. It also discriminates better: MEASURED over 6
    # independent no-signal seeds, the sharp count never exceeded 2 of 5, while
    # every signal seed reached 5 of 5 — so the majority rule (>= 3 of 5) sits
    # in a genuine gap. At 3 quantiles the rule is ">= 2 of 3" and a noise seed
    # reached it, giving an FPR of exactly 0.20 against a 0.20 bar.
    qs = tuple(DEFAULT_QUANTILES)
    need = len(qs) / 2.0
    fired = {True: 0, False: 0}
    detail = {True: [], False: []}
    for signal in (True, False):
        for sd in range(seeds):
            r = evaluate(_mk(n, signal, 1000 + sd), quantiles=qs,
                         control_trials=trials, seed=sd)
            c = sum(1 for x in r["sharpness"]
                    if x["sharpness_state"] == SHARP_BEATS_BASELINE)
            detail[signal].append(c)
            if c >= need:
                fired[signal] += 1
    tpr = fired[True] / seeds
    fpr = fired[False] / seeds
    print(f"[selftest] population: {seeds} seeds x n={n}, quantiles={qs}, "
          f"control_trials={trials}, majority rule >= {need} of {len(qs)}")
    print(f"[selftest] SIGNAL    fires {fired[True]}/{seeds} (TPR {tpr:.2f})  "
          f"sharp counts {detail[True]}")
    print(f"[selftest] NO-SIGNAL fires {fired[False]}/{seeds} (FPR {fpr:.2f})  "
          f"sharp counts {detail[False]}")
    ok = True
    if tpr < 0.8:
        print("[selftest] FAIL: does not find a signal that IS there (TPR < 0.80).")
        ok = False
    if fpr > 0.2:
        print("[selftest] FAIL: fires on noise too often (FPR > 0.20) — the null "
              "is not gating.")
        ok = False
    print("[selftest]", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", help="ml2_bracket_corpus.py output JSONL")
    ap.add_argument("--outcome", default="mfe_frac", choices=["mfe_frac", "exit_frac"])
    ap.add_argument("--split", type=float, default=0.65)
    ap.add_argument("--min-n", type=int, default=MIN_EVAL_N)
    ap.add_argument("--per-leg", action="store_true",
                    help="also evaluate each leg separately (expect insufficient_n)")
    ap.add_argument("--control-trials", type=int, default=20)
    ap.add_argument("--no-dispersion", action="store_true")
    ap.add_argument("--out", default=None, help="write the full result JSON here")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--selftest-seeds", type=int, default=6)
    args = ap.parse_args()

    if args.selftest:
        return selftest(seeds=args.selftest_seeds)
    if not args.corpus:
        ap.error("--corpus is required (or --selftest)")

    rows, malformed = _read_corpus(args.corpus)
    if malformed:
        print(f"⚠️  {malformed} malformed line(s) in {args.corpus} — "
              f"stated, not swallowed; the usable count below excludes them.")
    result = evaluate(rows, outcome=args.outcome, split=args.split,
                      min_n=args.min_n, control_trials=args.control_trials)
    # SAME control configuration as the headline — see dispersion()'s docstring.
    disp = None if args.no_dispersion else dispersion(
        rows, headline_verdict=result["verdict"], outcome=args.outcome,
        min_n=args.min_n, control_trials=args.control_trials)
    render(result, disp)

    per_leg = {}
    if args.per_leg:
        legs: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            legs.setdefault(str(r.get("leg")), []).append(r)
        print("\n--- PER-LEG (the population problem, measured rather than asserted) ---")
        print(f"{'leg':<34} {'n':>6} {'verdict':<34} {'mace':>8}")
        for leg, lr in sorted(legs.items()):
            res = evaluate(lr, outcome=args.outcome, min_n=args.min_n,
                           control_trials=args.control_trials)
            per_leg[leg] = res
            print(f"{leg:<34} {res['population']['usable']:>6} "
                  f"{res['verdict']:<34} {_fmt(res['mace']):>8}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        payload = {"headline": result, "dispersion": disp, "per_leg": per_leg}
        Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
