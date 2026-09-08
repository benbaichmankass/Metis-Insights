#!/usr/bin/env python3
"""Grade the ranking-key arms against the CONTROL — pooled-net AND fold-majority.

THE GATE IS A CONJUNCTION, AND THAT IS THE WHOLE POINT
------------------------------------------------------
This repo has already been burned by a fold majority read alone. `be_floor_r=1.5`
won a strict majority of exercised folds under all three panel members while
**LOSING 35R**, because 30-39% of its folds were INERT — the lever never fired,
the gate returned `ok`, and a no-op was counted as a win
(`BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`). So:

  * **inertness is IMPORTED**, from `scripts/research/m20_wf_effective.py::is_inert`,
    never restated here. A second definition of "this lever did not fire" is
    exactly how the two would drift, and this file would be the copy.
  * the majority is taken over **EFFECTIVE** folds only (won + lost), and
  * it must hold **TOGETHER WITH** pooled net R, never either alone.

THE CONTROL IS THE FLOOR, NOT A COURTESY ARM
--------------------------------------------
An arm is preferred only if it beats `random_tiebreak`. If the SHIPPED arm
(`confidence_first`) does not, the finding is that **arbitration ORDER does not
drive outcome** — publishable, and the signal to look at the confidence SCORE
itself instead (`BL-20260831-CONFIDENCE-SATURATES-AT-ONE-SO-HALF-OF-ARBITRATIONS-CANNOT-BE-DECIDED-ON-IT`). M18's adjacent
allocator already returned NO on a neighbouring question (edge -$7, ranker OOS
AUC ~0.51). **A NULL RESULT CLOSES THE QUESTION AND IS A SUCCESS**; it is not a
reason to re-run until something wins.

FOUR VERDICTS, NEVER COLLAPSED
------------------------------
  ``preferred``               — beat the control on BOTH halves of the gate.
  ``not_preferred``           — did not. A real, gradeable negative.
  ``inert``                   — the arm produced the SAME contested outcome as
                                the control on every effective fold: the key
                                never changed anything, which is not the same
                                claim as "it changed things and they were no
                                better".
  ``insufficient_population`` — the ACHIEVED contested n is below the floor, so
                                **nothing was graded**. Emphatically not a pass
                                and not a null: *we could not look*.

MULTIPLICITY IS DECLARED BEFORE ANY PAIRWISE READ
--------------------------------------------------
Three arms are compared against one control, so alpha is Bonferroni-corrected
to 0.05/3 = 0.0167 and that correction is printed with every p-value. Two
honesty limits are stated rather than buried: the test is a Welch two-sample
t on per-trade R with a NORMAL approximation to the tail (no scipy on the
runner), and **backtest trade sequences are autocorrelated**, so the iid p is a
LOWER bound on the true one (the design's own caveat 2). Treat a p near the
threshold as not established.

Usage:
    python3 scripts/research/rank_arm_report.py runtime_logs/ranking_ab/*.json
    python3 scripts/research/rank_arm_report.py --self-test
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# THE ONE definition of "this lever did not fire on this fold". IMPORTED, never
# restated: `BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS` is what a
# second copy of this predicate costs. Sibling-path import, the convention the
# rest of scripts/research uses (m20-exit-lever-sweep.yml imports `classify`
# the same way, and for the same stated reason).
from m20_wf_effective import is_inert  # noqa: E402

CONTROL_ARM = "random_tiebreak"
SHIPPED_ARM = "confidence_first"

#: Below this ACHIEVED contested-trade count nothing is graded. Chosen, not
#: measured: the design's own two-sample floor at d=0.2 is 393 PER GROUP, which
#: no BTCUSDT-only replay has ever reached, so a floor set there would refuse
#: every run and teach the next session to delete it. 30 is the point below
#: which a fold panel cannot carry more than a handful of trades per fold and
#: the majority half becomes noise. State it as a floor on GRADING, never as a
#: claim that 30 is adequate power — `power_note` says the opposite.
MIN_CONTESTED_TRADES = 30

#: Bonferroni over the three arm-vs-control comparisons. Declared here, before
#: any pairwise number is read, per the design's caveat (3).
ALPHA = 0.05
N_COMPARISONS = 3
ALPHA_CORRECTED = ALPHA / N_COMPARISONS


def _welch_p(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Two-sided Welch p for mean(a) != mean(b), NORMAL approximation.

    Returns None when either sample is too small to have a variance — an
    unmeasurable p must be `None`, never a 1.0 that reads as "tested, no
    difference".
    """
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    se2 = va / na + vb / nb
    if se2 <= 0:
        return None
    t = (ma - mb) / math.sqrt(se2)
    return math.erfc(abs(t) / math.sqrt(2.0))


def _fold_deltas(arm: Dict[str, Any], control: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-fold {d_net_r, d_max_dd} in the shape `is_inert` reads.

    ⚠️ The keys are `d_net_r` / `d_max_dd` deliberately: `is_inert` requires
    BOTH to be present and 0.0, and treats an ABSENT delta as NOT inert (a
    half-recorded fold is not a no-op). Emitting a differently-named key would
    make every fold read non-inert — the guard failing open, silently.
    """
    out: List[Dict[str, Any]] = []
    ctl = {f["fold"]: f for f in control.get("folds", [])}
    for f in arm.get("folds", []):
        c = ctl.get(f["fold"])
        if c is None:
            continue
        out.append({
            "fold": f["fold"], "start": f.get("start"), "end": f.get("end"),
            "d_net_r": round(float(f.get("net_r", 0.0)) - float(c.get("net_r", 0.0)), 6),
            "d_max_dd": round(float(f.get("max_drawdown_usd", 0.0))
                              - float(c.get("max_drawdown_usd", 0.0)), 6),
            "arm_trades": f.get("trades"), "control_trades": c.get("trades"),
        })
    return out


def grade_arm(arm: Dict[str, Any], control: Dict[str, Any]) -> Dict[str, Any]:
    """Grade ONE arm against the control. Pure — takes two run payloads."""
    a_c = arm.get("contested", {})
    c_c = control.get("contested", {})
    n_arm = int(a_c.get("trades", 0) or 0)
    n_ctl = int(c_c.get("trades", 0) or 0)

    folds = _fold_deltas(arm, control)
    inert = [f for f in folds if is_inert(f)]
    effective = [f for f in folds if not is_inert(f)]
    won = [f for f in effective if f["d_net_r"] > 0]
    lost = [f for f in effective if f["d_net_r"] < 0]
    # d_net_r == 0 with a non-zero d_max_dd is EXERCISED but net-flat: it is
    # neither a win nor a loss, and folding it into either would be the same
    # error `is_inert`'s conjunction exists to prevent one level down.
    flat = [f for f in effective if f["d_net_r"] == 0]

    d_pooled_r = round(float(a_c.get("net_r", 0.0)) - float(c_c.get("net_r", 0.0)), 4)
    d_pooled_pnl = round(float(a_c.get("net_pnl_usd", 0.0))
                         - float(c_c.get("net_pnl_usd", 0.0)), 2)
    d_max_dd = round(float(a_c.get("max_drawdown_usd", 0.0))
                     - float(c_c.get("max_drawdown_usd", 0.0)), 2)
    p = _welch_p(a_c.get("r_values", []), c_c.get("r_values", []))

    pooled_ok = d_pooled_r > 0
    majority_ok = len(won) > len(lost) and len(effective) > 0

    if min(n_arm, n_ctl) < MIN_CONTESTED_TRADES:
        verdict = "insufficient_population"
    elif not effective:
        verdict = "inert"
    elif pooled_ok and majority_ok:
        verdict = "preferred"
    else:
        verdict = "not_preferred"

    return {
        "arm": arm.get("ranking_key"),
        "verdict": verdict,
        "population": {
            "arm_contested_trades": n_arm,
            "control_contested_trades": n_ctl,
            "arm_contested_elections_achieved":
                arm.get("population", {}).get("elections_contested_achieved"),
            "control_contested_elections_achieved":
                control.get("population", {}).get("elections_contested_achieved"),
            "min_contested_trades_floor": MIN_CONTESTED_TRADES,
        },
        "pooled": {
            "arm_net_r": a_c.get("net_r"), "control_net_r": c_c.get("net_r"),
            "d_net_r": d_pooled_r,
            "arm_net_pnl_usd": a_c.get("net_pnl_usd"),
            "control_net_pnl_usd": c_c.get("net_pnl_usd"),
            "d_net_pnl_usd": d_pooled_pnl,
            "arm_expectancy_r": a_c.get("expectancy_r"),
            "control_expectancy_r": c_c.get("expectancy_r"),
            "arm_max_drawdown_usd": a_c.get("max_drawdown_usd"),
            "control_max_drawdown_usd": c_c.get("max_drawdown_usd"),
            "d_max_drawdown_usd": d_max_dd,
            "pooled_net_r_positive": pooled_ok,
        },
        "fold_panel": {
            "folds": len(folds), "effective": len(effective), "inert": len(inert),
            "won": len(won), "lost": len(lost), "flat_but_exercised": len(flat),
            "majority_of_effective": majority_ok,
            "inert_share_pct": (round(100.0 * len(inert) / len(folds), 1)
                                if folds else None),
            "rows": folds,
        },
        "significance": {
            "welch_p_normal_approx": (round(p, 5) if p is not None else None),
            "alpha_declared": ALPHA,
            "comparisons": N_COMPARISONS,
            "alpha_bonferroni": round(ALPHA_CORRECTED, 5),
            "clears_corrected_alpha": (None if p is None else p < ALPHA_CORRECTED),
            "caveat": ("iid two-sample normal approximation. Backtest trade "
                       "sequences are AUTOCORRELATED, so this p is a LOWER "
                       "BOUND on the true one — a value near the threshold is "
                       "not established."),
        },
        "by_decided_by": {
            term: {
                "arm": arm.get("contested_by_decided_by", {}).get(term, {}),
                "control": control.get("contested_by_decided_by", {}).get(term, {}),
            }
            for term in sorted(set(arm.get("contested_by_decided_by", {}))
                               | set(control.get("contested_by_decided_by", {})))
        },
    }


def _merge_reasons(run: Dict[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for b in run.get("books", []):
        for k, v in (b.get("by_exit_reason") or {}).items():
            out[k] = out.get(k, 0) + int(v)
    return dict(sorted(out.items()))


def _comparability(runs: Dict[str, Dict[str, Any]]) -> List[str]:
    """Refuse to grade arms that did not measure the same thing.

    A fold panel or a cost basis that differs between arms makes every delta
    below meaningless while the report still renders — the shape this repo
    calls unprovenanced diagnostic output. So the mismatch FAILS rather than
    being noted in small print.
    """
    problems: List[str] = []
    keys = ("data_start", "data_end", "clock_bars", "symbol", "arbitration")
    ref_name, ref = next(iter(runs.items()))
    for name, r in runs.items():
        for k in keys:
            if r.get(k) != ref.get(k):
                problems.append(f"{name}.{k}={r.get(k)!r} != {ref_name}.{k}={ref.get(k)!r}")
        for k in ("folds", "clock_tf", "signal_ttl_bars", "flip_policy",
                  "fee_bps_roundtrip", "slippage_bps_roundtrip",
                  "funding_bps_per_window", "union_roster"):
            if r.get("params", {}).get(k) != ref.get("params", {}).get(k):
                problems.append(f"{name}.params.{k} != {ref_name}.params.{k}")
    return problems


def build_report(runs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    if CONTROL_ARM not in runs:
        raise ValueError(
            f"the CONTROL arm {CONTROL_ARM!r} is absent. It is the floor every "
            f"other arm must clear, so a report without it grades nothing — "
            f"see docs/research/trade-prioritisation-research-DESIGN.yaml.")
    problems = _comparability(runs)
    control = runs[CONTROL_ARM]
    arms = {name: grade_arm(r, control) for name, r in sorted(runs.items())
            if name != CONTROL_ARM}
    shipped = arms.get(SHIPPED_ARM, {})
    return {
        "kind": "ranking_key_ab_report",
        "control_arm": CONTROL_ARM,
        "shipped_arm": SHIPPED_ARM,
        "comparability_problems": problems,
        "gradeable": not problems,
        "gate": ("pooled net R > 0 AND a majority of EFFECTIVE folds, never "
                 "either alone. Inertness is imported from "
                 "m20_wf_effective.is_inert (both deltas)."),
        "population_note": (
            "Every number is the CONTESTED subset. The ACHIEVED contested count "
            "is reported per arm; grade power against it, never against the "
            "design's declared expected_n=1780."),
        "power_note": (
            f"The design's two-sample floor at d=0.2 is 393 PER GROUP. "
            f"MIN_CONTESTED_TRADES={MIN_CONTESTED_TRADES} is a floor on GRADING "
            f"AT ALL, not a claim of adequate power. An arm graded above it and "
            f"below 393 is measured but UNDERPOWERED, and must be reported as "
            f"such."),
        "headline": {
            "shipped_beats_control": shipped.get("verdict") == "preferred",
            "shipped_verdict": shipped.get("verdict"),
            "any_arm_preferred": any(a["verdict"] == "preferred" for a in arms.values()),
        },
        "control": {
            "contested_trades": control.get("contested", {}).get("trades"),
            "net_r": control.get("contested", {}).get("net_r"),
            "net_pnl_usd": control.get("contested", {}).get("net_pnl_usd"),
            "expectancy_r": control.get("contested", {}).get("expectancy_r"),
            "max_drawdown_usd": control.get("contested", {}).get("max_drawdown_usd"),
            "seed": control.get("seed"),
        },
        "arms": arms,
        # ⚠️ THE QUESTION THE OPERATOR ACTUALLY ASKED, and it is NOT the one the
        # control answers. Beating `random_tiebreak` says a stable order beats
        # an unstable one; it does not say CONFIDENCE is the right stable order.
        # *"the confidence score is a good start, but it isn't proven that it's
        # necessarily what's gonna give us the best trade every time"* is an
        # arm-vs-arm claim, so the shipped arm is also graded against each
        # counterfactual directly. `preferred` here means the SHIPPED key beat
        # that alternative on the same conjunction.
        "head_to_head_vs_shipped": (
            {name: grade_arm(runs[SHIPPED_ARM], r)
             for name, r in sorted(runs.items())
             if name not in (SHIPPED_ARM, CONTROL_ARM)}
            if SHIPPED_ARM in runs else {}),
        "head_to_head_note": (
            "Each entry grades the SHIPPED arm AGAINST that alternative as the "
            "reference. `verdict: preferred` = confidence_first beat it; "
            "`not_preferred` = it did not, which is the finding the operator's "
            "question is about."),
        # The instability confound, surfaced rather than buried: the control's
        # per-tick re-roll changes the winning OWNER between ticks, so some of
        # its deficit is churn rather than a worse ranking. Flip counts make the
        # two separable; a reader who quotes the pooled delta without them is
        # attributing to the ranking signal something the flip count may explain.
        "churn": {
            name: {"total_trades": sum(b.get("total_trades", 0) for b in r.get("books", [])),
                   "by_exit_reason": _merge_reasons(r)}
            for name, r in sorted(runs.items())
        },
    }


def fmt(rep: Dict[str, Any]) -> str:
    L = ["ranking-key A/B — CONTESTED SUBSET ONLY",
         f"  gate: {rep['gate']}",
         f"  control ({rep['control_arm']}, seed={rep['control']['seed']}): "
         f"trades={rep['control']['contested_trades']} "
         f"netR={rep['control']['net_r']} net=${rep['control']['net_pnl_usd']} "
         f"expectancyR={rep['control']['expectancy_r']} "
         f"maxDD=${rep['control']['max_drawdown_usd']}"]
    if rep["comparability_problems"]:
        L.append("  ⚠️ NOT GRADEABLE — the arms did not measure the same thing:")
        for p in rep["comparability_problems"][:8]:
            L.append(f"      {p}")
    for name, a in sorted(rep["arms"].items()):
        fp = a["fold_panel"]
        sig = a["significance"]
        L.append(f"  {name:18s} verdict={a['verdict']}")
        L.append(f"      pooled  d_netR={a['pooled']['d_net_r']:+.4f} "
                 f"d_net=${a['pooled']['d_net_pnl_usd']:+.2f} "
                 f"d_maxDD=${a['pooled']['d_max_drawdown_usd']:+.2f} "
                 f"expectancyR {a['pooled']['arm_expectancy_r']} vs "
                 f"{a['pooled']['control_expectancy_r']}")
        L.append(f"      folds   won={fp['won']} lost={fp['lost']} "
                 f"flat_exercised={fp['flat_but_exercised']} INERT={fp['inert']}"
                 f"/{fp['folds']} ({fp['inert_share_pct']}%) "
                 f"-> majority_of_effective={fp['majority_of_effective']}")
        L.append(f"      n       arm={a['population']['arm_contested_trades']} "
                 f"control={a['population']['control_contested_trades']} "
                 f"(floor {a['population']['min_contested_trades_floor']})")
        L.append(f"      p       {sig['welch_p_normal_approx']} vs alpha_bonf "
                 f"{sig['alpha_bonferroni']} -> clears={sig['clears_corrected_alpha']}")
    if rep.get("head_to_head_vs_shipped"):
        L.append(f"  HEAD-TO-HEAD — {rep['shipped_arm']} vs each alternative "
                 f"(this, not the control, is the operator's question):")
        for name, h in sorted(rep["head_to_head_vs_shipped"].items()):
            fp = h["fold_panel"]
            L.append(f"    vs {name:18s} verdict={h['verdict']} "
                     f"d_netR={h['pooled']['d_net_r']:+.4f} "
                     f"d_net=${h['pooled']['d_net_pnl_usd']:+.2f} "
                     f"folds won={fp['won']} lost={fp['lost']} inert={fp['inert']}"
                     f"/{fp['folds']} p={h['significance']['welch_p_normal_approx']}")
    if rep.get("churn"):
        L.append("  CHURN (the control re-rolls per tick, so part of its deficit "
                 "is instability, not ranking):")
        for name, ch in sorted(rep["churn"].items()):
            flips = ch["by_exit_reason"].get("flip", 0)
            L.append(f"    {name:18s} trades={ch['total_trades']:5d} flips={flips:5d} "
                     f"{ch['by_exit_reason']}")
    L.append(f"  HEADLINE: shipped arm ({rep['shipped_arm']}) verdict="
             f"{rep['headline']['shipped_verdict']}; any arm preferred="
             f"{rep['headline']['any_arm_preferred']}")
    if rep["headline"]["shipped_verdict"] == "not_preferred":
        L.append("  → A NULL RESULT IS A REAL RESULT: arbitration ORDER did not "
                 "drive outcome on this population. The follow-up is the "
                 "confidence SCORE, not more ranking work.")
    return "\n".join(L)


# --------------------------------------------------------------------------
def _self_test() -> int:
    def run(arm, *, net_r, pnl, mdd, folds, rvals, trades=100):
        return {
            "kind": "nbook_ranking_backtest", "ranking_key": arm, "seed": 7,
            "symbol": "BTCUSDT", "arbitration": "per_account",
            "data_start": "A", "data_end": "B", "clock_bars": 10,
            "params": {"folds": len(folds), "clock_tf": "15m",
                       "signal_ttl_bars": 1, "flip_policy": "reverse",
                       "fee_bps_roundtrip": 7.5, "slippage_bps_roundtrip": 5.0,
                       "funding_bps_per_window": 1.0, "union_roster": ["x"]},
            "population": {"elections_contested_achieved": 500},
            "contested": {"trades": trades, "net_r": net_r, "net_pnl_usd": pnl,
                          "expectancy_r": net_r / max(trades, 1),
                          "max_drawdown_usd": mdd, "r_values": rvals},
            "contested_by_decided_by": {},
            "folds": [{"fold": k, "net_r": v, "max_drawdown_usd": m, "trades": 10,
                       "start": str(k), "end": str(k + 1)}
                      for k, (v, m) in enumerate(folds)],
        }

    ctl = run(CONTROL_ARM, net_r=0.0, pnl=0.0, mdd=100.0,
              folds=[(0.0, 100.0)] * 6, rvals=[0.0] * 100)

    # 1) An arm that wins pooled AND on effective folds is preferred.
    good = run("confidence_first", net_r=6.0, pnl=600.0, mdd=90.0,
               folds=[(1.0, 90.0)] * 4 + [(-1.0, 110.0)] * 2, rvals=[0.06] * 100)
    g = grade_arm(good, ctl)
    assert g["verdict"] == "preferred", g["verdict"]
    assert g["fold_panel"] == g["fold_panel"] and g["fold_panel"]["won"] == 4

    # 2) THE be_floor_r=1.5 SHAPE: a strict majority of EXERCISED folds while
    #    LOSING pooled, with a third of the folds inert. The conjunction must
    #    refuse it; a fold-majority-only gate would have passed it.
    trap = run("priority_first", net_r=-35.0, pnl=-3500.0, mdd=120.0,
               folds=[(0.0, 100.0)] * 2          # INERT — both deltas zero
                     + [(1.0, 100.0)] * 3        # "wins", tiny
                     + [(-38.0, 100.0)],         # one huge loss
               rvals=[-0.35] * 100)
    t = grade_arm(trap, ctl)
    assert t["fold_panel"]["inert"] == 2, t["fold_panel"]
    assert t["fold_panel"]["won"] == 3 and t["fold_panel"]["lost"] == 1
    assert t["fold_panel"]["majority_of_effective"] is True, "fold majority DID hold"
    assert t["pooled"]["pooled_net_r_positive"] is False
    assert t["verdict"] == "not_preferred", (
        "the conjunction must refuse an arm that wins the fold majority while "
        "losing pooled — this is the be_floor_r=1.5 failure")

    # 3) An arm identical to the control on every fold is INERT, which is NOT
    #    the same claim as "graded and no better".
    same = run("recent_pnl_first", net_r=0.0, pnl=0.0, mdd=100.0,
               folds=[(0.0, 100.0)] * 6, rvals=[0.0] * 100)
    assert grade_arm(same, ctl)["verdict"] == "inert"

    # 4) Below the floor NOTHING is graded — not a pass, not a null.
    thin = run("confidence_first", net_r=9.0, pnl=900.0, mdd=10.0,
               folds=[(1.0, 90.0)] * 6, rvals=[0.5] * 5, trades=5)
    assert grade_arm(thin, ctl)["verdict"] == "insufficient_population"

    # 5) A fold with a MISSING delta must not read as inert (is_inert's own
    #    contract), so a half-recorded panel cannot silently shrink `effective`.
    assert not is_inert({"d_net_r": 0.0})
    assert not is_inert({})

    # 6) The control is mandatory.
    try:
        build_report({"confidence_first": good})
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a report without the control must be refused")

    # 7) Arms that measured different things are NOT gradeable.
    other = run("priority_first", net_r=1.0, pnl=1.0, mdd=1.0,
                folds=[(1.0, 1.0)] * 6, rvals=[0.01] * 100)
    other["data_end"] = "DIFFERENT"
    rep = build_report({CONTROL_ARM: ctl, "confidence_first": good,
                        "priority_first": other})
    assert rep["gradeable"] is False and rep["comparability_problems"]

    # 8) Multiplicity is declared, and an unmeasurable p is None not 1.0.
    rep2 = build_report({CONTROL_ARM: ctl, "confidence_first": good})
    assert rep2["arms"]["confidence_first"]["significance"]["alpha_bonferroni"] \
        == round(0.05 / 3, 5)
    assert _welch_p([1.0], [2.0]) is None

    print("rank_arm_report self-test: OK "
          f"(gate=pooled AND fold-majority; inertness imported; "
          f"alpha_bonf={ALPHA_CORRECTED:.5f})")
    return 0


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("paths", nargs="*", help="Per-arm JSON payloads.")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv[1:])
    if args.self_test:
        return _self_test()
    if not args.paths:
        p.print_help()
        return 1
    runs: Dict[str, Dict[str, Any]] = {}
    for path in args.paths:
        payload = json.loads(Path(path).read_text())
        arm = payload.get("ranking_key")
        if not arm:
            print(f"ERROR: {path} carries no `ranking_key`", file=sys.stderr)
            return 1
        if arm in runs:
            print(f"ERROR: two payloads for arm {arm!r} — refusing rather than "
                  f"silently taking one", file=sys.stderr)
            return 1
        runs[arm] = payload
    try:
        rep = build_report(runs)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(fmt(rep))
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(rep, indent=2, default=str))
        print(f"JSON -> {args.json_out}", file=sys.stderr)
    return 0 if rep["gradeable"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
