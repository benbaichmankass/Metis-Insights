#!/usr/bin/env python3
# wiring: manual-only — a one-shot audit answering "can the rows in this R
#   aggregate register an R at all?". Run BY A SESSION against a live
#   /api/bot/performance payload; scheduling it would re-ask the question over a
#   window nobody chose.
"""MI-278 U46 — can the rows in this R aggregate register an R at all?

THE ROW, AND THE CLAUSE THAT IS LEFT
-------------------------------------
`PB-20260821-R-AND-DOLLARS-DISAGREE-IN-SIGN` has a two-clause, CONJUNCTIVE
`resolution_criteria`:

  (1) no per-strategy ``expectancyR`` exceeds a stated plausible bound without
      an explicit outlier annotation, and
  (2) the real-money ``totalR`` sign matches its ``totalPnl`` sign.

Clause (2) was addressed by MI-278 U36, which established the surviving sign
disagreement as **risk-size heterogeneity** — what a risk-normalised statistic
DOES when winners are sized smaller than losers — and says in terms *"do not
'fix' the endpoint to make them agree"*. This module is about clause (1), and
about a defect found while checking it that neither clause names.

THE DEFECT: rCoverage CANNOT SAY "THE DENOMINATOR IS NOT A RISK LEVEL"
-----------------------------------------------------------------------
``rCoverage`` answers *"does this row have a risk denominator?"*. It does not
answer *"is that denominator a risk distance?"*, and the two are different
questions on this fleet. A leg whose stop is a **sentinel at 50–100% of the
entry price** has a denominator — an enormous one — so it counts as covered,
and its ``R = pnl / (qty × entry × 0.5..1.0)`` is a **return on NOTIONAL**, not
a risk multiple. Such a row contributes ~0 to ``totalR`` whatever it does in
dollars, and since ``expectancyR`` divides by the row COUNT, every one of them
drags the aggregate toward zero.

⚠️ **THIS IS THE MIRROR OF THE DEFECT THE ROW WAS FILED ABOUT, AND POINTS THE
OTHER WAY.** The row feared a denominator collapsing toward zero, making R
EXPLODE (its headline was 206.920 R/trade). A sentinel denominator makes R
VANISH, which is quiet, looks healthy, and understates the aggregate.

THREE AXES, NEVER COLLAPSED INTO ONE VERDICT
---------------------------------------------
* ``basis`` — WHERE the denominator came from, taken from the endpoint's own
  ``rBasis``: ``declared_initial`` · ``stored_stop`` (the column
  ``order_monitor`` mutates on every trailing amend) · ``refused_wrong_side``
  · ``no_basis`` · ``unknown``.
* ``scale`` — WHETHER it is a risk distance, from the package geometry:
  ``risk_level`` · ``notional_sentinel`` (``|entry − sl| / entry`` at or above
  ``--sentinel-frac``, default 0.25) · ``unknown`` — ***we did not look***,
  which is the state whenever no packages pull is supplied.
* ``admissible`` — a row is admissible only when BOTH are known AND neither is
  disqualifying. Anything else is inadmissible-or-unknown, and the two are
  reported apart.

⚠️ **WITHOUT ``--packages`` THIS MODULE REFUSES TO GRADE ``scale``**, and
therefore refuses to publish an admissible share. A basis-only run reports the
basis census and says so. Printing an admissibility figure from ``rBasis``
alone would reproduce the exact collapse it exists to expose — the endpoint
already reports 1.0 there.

WHAT IT DOES NOT DO
-------------------
It changes nothing. `src/web/api/routers/performance.py` and
`src/web/api/_clean_trades.py` are Tier-2 and are not touched; whether the
endpoint should EXCLUDE a leg from an R aggregate is a decision, and a decision
about what a promotion gate reads is **Tier-3**. It emits no verdict on any
leg's edge, and it does not recompute PnL.

Usage:
  python3 scripts/research/r_denominator_admissibility.py --self-test
  python3 scripts/research/r_denominator_admissibility.py --performance perf.json
  python3 scripts/research/r_denominator_admissibility.py --performance perf.json --packages pkg.json
  python3 scripts/research/r_denominator_admissibility.py --performance perf.json --packages pkg.json --json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

# --- basis: where the denominator came from (the endpoint's own rBasis) ---
BASIS_DECLARED = "declared_initial"
BASIS_STORED = "stored_stop"
BASIS_REFUSED = "refused_wrong_side"
BASIS_NONE = "no_basis"
BASIS_UNKNOWN = "unknown"
_BASIS_KEYS = {
    "declaredInitial": BASIS_DECLARED,
    "storedStop": BASIS_STORED,
    "refusedWrongSide": BASIS_REFUSED,
    "noBasis": BASIS_NONE,
}

# --- scale: whether that denominator is a risk distance at all ---
SCALE_RISK = "risk_level"
SCALE_SENTINEL = "notional_sentinel"
SCALE_UNKNOWN = "unknown"

# A stop this far from entry is not a risk level on any instrument this fleet
# trades. MEASURED 2026-09-17 over 1000 order_packages: the non-pairs
# |entry-sl|/entry distribution runs 0.0001 -> 0.1440 (median 0.0107) and the
# pairs distribution runs 0.5000 -> 1.0000 (median 1.0000). 0.25 sits in the
# empty gap between them by a factor of ~1.7 on one side and 2.0 on the other;
# it is a CHOSEN value with a measured basis, not a tuned one, and it is a flag
# so a different fleet can move it.
DEFAULT_SENTINEL_FRAC = 0.25


def _num(v):
    """float(v) or None. Never raises; a bool is not a number here."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def basis_census(r_basis):
    """``rBasis`` -> {state: count}. An absent/unusable block is ``unknown``.

    ``unknown`` is ***we could not look***, never ``no_basis`` — the endpoint
    saying "this row has no risk basis" and us failing to read the field are
    opposite statements.
    """
    out = collections.Counter()
    if not isinstance(r_basis, dict):
        return {BASIS_UNKNOWN: None}
    seen = False
    for key, state in _BASIS_KEYS.items():
        n = _num(r_basis.get(key))
        if n is None:
            continue
        seen = True
        out[state] += int(n)
    if not seen:
        return {BASIS_UNKNOWN: None}
    return dict(out)


def stop_fraction(entry, sl):
    """``|entry - sl| / entry``, or None. Scale-free, so one bar serves every
    instrument."""
    e, s = _num(entry), _num(sl)
    if e is None or s is None or e <= 0:
        return None
    return abs(e - s) / e


def scale_state(entry, sl, sentinel_frac=DEFAULT_SENTINEL_FRAC):
    """``risk_level`` / ``notional_sentinel`` / ``unknown``.

    ``unknown`` on unusable geometry, and it is NOT ``risk_level``: assuming a
    row we could not measure is fine is how an inadmissible population passes
    as a clean one.
    """
    f = stop_fraction(entry, sl)
    if f is None:
        return SCALE_UNKNOWN
    return SCALE_SENTINEL if f >= float(sentinel_frac) else SCALE_RISK


def leg_scale(packages, strategy, sentinel_frac=DEFAULT_SENTINEL_FRAC):
    """Grade a LEG from its packages. Returns ``(state, n_graded, n_sentinel)``.

    A leg is ``notional_sentinel`` only when EVERY gradeable package of it is
    one — a mixed leg grades ``unknown`` and is named, because "half this leg's
    denominators are sentinels" is a finding, not a rounding decision.
    """
    rows = [p for p in (packages or []) if p.get("strategy_name") == strategy]
    states = [scale_state(p.get("entry"), p.get("sl"), sentinel_frac)
              for p in rows]
    graded = [s for s in states if s != SCALE_UNKNOWN]
    n_sentinel = sum(1 for s in graded if s == SCALE_SENTINEL)
    if not graded:
        return SCALE_UNKNOWN, 0, 0
    if n_sentinel == len(graded):
        return SCALE_SENTINEL, len(graded), n_sentinel
    if n_sentinel == 0:
        return SCALE_RISK, len(graded), 0
    return SCALE_UNKNOWN, len(graded), n_sentinel


def grade_block(block, packages=None, *, sentinel_frac=DEFAULT_SENTINEL_FRAC,
                label="block"):
    """Grade one `/api/bot/performance` block (top level, or `paper`, …)."""
    out = {
        "label": label,
        "reported": {},
        "basis": {},
        "scale_graded": packages is not None,
        "per_leg": {},
        "reconciles": {},
        "admissible": None,
        "restated": None,
        "max_abs_expectancy_r": None,
        "max_abs_expectancy_r_leg": None,
    }
    if not isinstance(block, dict):
        out["reported"] = {"error": "block is not a mapping"}
        return out
    for k in ("totalTrades", "totalPnl", "totalR", "expectancyR",
              "rTradeCount", "rCoverage", "pnlCoverage"):
        out["reported"][k] = block.get(k)
    out["basis"] = basis_census(block.get("rBasis"))

    legs = block.get("perStrategy") or []
    if not isinstance(legs, list):
        legs = []

    adm_n = adm_r = 0
    inadm_n = inadm_r = 0
    unk_n = unk_r = 0
    adm_pnl = inadm_pnl = 0.0
    leg_trades = 0
    leg_r = 0.0
    best = None
    for leg in legs:
        name = str(leg.get("name") or "")
        n = int(_num(leg.get("trades")) or 0)
        tr = _num(leg.get("totalR")) or 0.0
        tp = _num(leg.get("totalPnl")) or 0.0
        er = _num(leg.get("expectancyR"))
        leg_trades += n
        leg_r += tr
        if er is not None and (best is None or abs(er) > abs(best[1])):
            best = (name, er)
        rb = leg.get("rBasis") or {}
        stored = int(_num(rb.get("storedStop")) or 0)
        declared = int(_num(rb.get("declaredInitial")) or 0)
        if packages is None:
            sc, graded, n_sent = SCALE_UNKNOWN, 0, 0
        else:
            sc, graded, n_sent = leg_scale(packages, name, sentinel_frac)
        row = {
            "trades": n, "totalPnl": round(tp, 4), "totalR": round(tr, 4),
            "expectancyR": er,
            "basis_declared": declared, "basis_stored": stored,
            "scale": sc, "packages_graded": graded,
            "packages_sentinel": n_sent,
        }
        if sc == SCALE_SENTINEL:
            row["admissible"] = False
            inadm_n += n
            inadm_r += tr
            inadm_pnl += abs(tp)
        elif sc == SCALE_RISK:
            row["admissible"] = True
            adm_n += n
            adm_r += tr
            adm_pnl += abs(tp)
        else:
            row["admissible"] = None
            unk_n += n
            unk_r += tr
        out["per_leg"][name] = row

    if best is not None:
        out["max_abs_expectancy_r_leg"] = best[0]
        out["max_abs_expectancy_r"] = best[1]

    # The assertion inside the transform: the per-leg rows must sum to the
    # block the endpoint reported, or no figure below means anything.
    rep_n = _num(block.get("totalTrades"))
    rep_r = _num(block.get("totalR"))
    out["reconciles"] = {
        "per_leg_trades": leg_trades,
        "reported_trades": rep_n,
        "per_leg_total_r": round(leg_r, 4),
        "reported_total_r": rep_r,
        "ok": (rep_n is not None and leg_trades == int(rep_n)
               and rep_r is not None and abs(leg_r - rep_r) < 1e-2),
    }

    if packages is None:
        # Refusing on purpose: an admissible share derived from rBasis alone
        # would repeat the very collapse this module exists to expose.
        return out

    graded_n = adm_n + inadm_n
    out["admissible"] = {
        "admissible_trades": adm_n,
        "inadmissible_trades": inadm_n,
        "ungraded_trades": unk_n,
        "admissible_share": (round(adm_n / graded_n, 4) if graded_n else None),
        "inadmissible_share_of_block": (
            round(inadm_n / int(rep_n), 4) if rep_n else None),
        "inadmissible_share_of_abs_pnl": (
            round(inadm_pnl / (adm_pnl + inadm_pnl), 4)
            if (adm_pnl + inadm_pnl) else None),
    }
    out["restated"] = {
        "expectancy_r_as_reported": (
            round(rep_r / rep_n, 5) if rep_n else None),
        "expectancy_r_admissible_only": (
            round(adm_r / adm_n, 5) if adm_n else None),
        "ratio": (round((adm_r / adm_n) / (rep_r / rep_n), 3)
                  if adm_n and rep_n and rep_r else None),
    }
    return out


def render(v, bound=None):
    L = [f"R-DENOMINATOR ADMISSIBILITY — {v['label']}"]
    rep = v["reported"]
    if rep.get("error"):
        L.append(f"  REFUSED: {rep['error']}")
        return "\n".join(L)
    L.append(f"  reported: n={rep.get('totalTrades')}  totalPnl={rep.get('totalPnl')}  "
             f"totalR={rep.get('totalR')}  expectancyR={rep.get('expectancyR')}  "
             f"rCoverage={rep.get('rCoverage')}")
    L.append("  rBasis census (WHERE the denominator came from): "
             + json.dumps(v["basis"]))
    rc = v["reconciles"]
    L.append(f"  reconciliation: per-leg trades {rc['per_leg_trades']} vs reported "
             f"{rc['reported_trades']} · per-leg totalR {rc['per_leg_total_r']} vs "
             f"{rc['reported_total_r']} -> "
             + ("OK" if rc["ok"] else "⚠️ DISAGREES — quote nothing below"))
    m, mleg = v.get("max_abs_expectancy_r"), v.get("max_abs_expectancy_r_leg")
    if m is not None:
        line = f"  largest |expectancyR| on any leg: {m:+.4f} ({mleg})"
        if bound is not None:
            line += ("  — WITHIN the stated bound %.2f" % bound if abs(m) <= bound
                     else "  — EXCEEDS the stated bound %.2f" % bound)
        L.append(line)
    if not v["scale_graded"]:
        L.append("")
        L.append("  ⚠️ SCALE NOT GRADED — no --packages pull was supplied, so whether each")
        L.append("     denominator is a RISK DISTANCE is *we did not look*. No admissible")
        L.append("     share is published: deriving one from rBasis alone would repeat the")
        L.append("     collapse this module exists to expose (rCoverage already reads 1.0).")
        return "\n".join(L)
    a, r = v["admissible"], v["restated"]
    L.append("")
    L.append("  ADMISSIBILITY (can the row register an R at all?):")
    L.append(f"     admissible (denominator is a risk level) : {a['admissible_trades']}")
    L.append(f"     inadmissible (notional sentinel)         : {a['inadmissible_trades']}")
    L.append(f"     ungraded (we did not look)               : {a['ungraded_trades']}")
    L.append(f"     admissibleShare                          : {a['admissible_share']}")
    L.append(f"     inadmissible share of the R population   : {a['inadmissible_share_of_block']}")
    L.append(f"     inadmissible share of the block's |PnL|  : {a['inadmissible_share_of_abs_pnl']}")
    L.append("")
    L.append("  RESTATED OVER ADMISSIBLE ROWS ONLY — printed BESIDE the reported figure,")
    L.append("  never instead of it:")
    L.append(f"     expectancyR as reported        : {r['expectancy_r_as_reported']}")
    L.append(f"     expectancyR, admissible rows   : {r['expectancy_r_admissible_only']}")
    L.append(f"     ratio                          : {r['ratio']}x")
    sent = [k for k, x in v["per_leg"].items() if x["scale"] == SCALE_SENTINEL]
    mixed = [k for k, x in v["per_leg"].items()
             if x["scale"] == SCALE_UNKNOWN and x["packages_sentinel"]]
    if sent:
        L.append("  legs whose EVERY gradeable package carries a notional sentinel: "
                 + ", ".join(sorted(sent)))
    if mixed:
        L.append("  ⚠️ MIXED legs (some packages sentinel, some not) — graded `unknown` and "
                 "named rather than rounded: " + ", ".join(sorted(mixed)))
    return "\n".join(L)


# ---------------------------------------------------------------------------
def _self_test():
    fails, ran = [], [0]

    def ck(name, cond):
        ran[0] += 1
        if not cond:
            fails.append(name)

    # --- stop_fraction / scale_state ---------------------------------------
    ck("stop fraction is scale-free",
       stop_fraction(100.0, 101.0) == stop_fraction(1000.0, 1010.0))
    ck("stop fraction None on zero entry", stop_fraction(0.0, 1.0) is None)
    ck("stop fraction None on a missing stop", stop_fraction(100.0, None) is None)
    ck("a 1% stop is a risk level",
       scale_state(100.0, 101.0) == SCALE_RISK)
    ck("a 100% stop is a notional sentinel",
       scale_state(100.0, 200.0) == SCALE_SENTINEL)
    ck("a 50% stop is a notional sentinel",
       scale_state(100.0, 50.0) == SCALE_SENTINEL)
    ck("the bar is inclusive at the frac",
       scale_state(100.0, 125.0) == SCALE_SENTINEL)
    ck("just inside the bar is a risk level",
       scale_state(100.0, 124.9) == SCALE_RISK)
    ck("unusable geometry is unknown, never risk_level",
       scale_state(None, 1.0) == SCALE_UNKNOWN)
    ck("the bar is a flag, not a constant",
       scale_state(100.0, 110.0, sentinel_frac=0.05) == SCALE_SENTINEL)

    # --- basis_census -------------------------------------------------------
    ck("basis census reads the endpoint's own keys",
       basis_census({"declaredInitial": 41, "storedStop": 0,
                     "refusedWrongSide": 0, "noBasis": 0})
       == {BASIS_DECLARED: 41, BASIS_STORED: 0, BASIS_REFUSED: 0, BASIS_NONE: 0})
    ck("an absent rBasis is unknown, never no_basis",
       basis_census(None) == {BASIS_UNKNOWN: None})
    ck("an rBasis with no recognised key is unknown",
       basis_census({"somethingElse": 3}) == {BASIS_UNKNOWN: None})

    # --- leg_scale ----------------------------------------------------------
    pk = [
        {"strategy_name": "pairs_a", "entry": 100.0, "sl": 200.0},
        {"strategy_name": "pairs_a", "entry": 100.0, "sl": 50.0},
        {"strategy_name": "scalp", "entry": 100.0, "sl": 100.5},
        {"strategy_name": "mixed", "entry": 100.0, "sl": 200.0},
        {"strategy_name": "mixed", "entry": 100.0, "sl": 100.5},
        {"strategy_name": "blind", "entry": None, "sl": None},
    ]
    ck("an all-sentinel leg grades sentinel", leg_scale(pk, "pairs_a")[0] == SCALE_SENTINEL)
    ck("an all-risk leg grades risk", leg_scale(pk, "scalp")[0] == SCALE_RISK)
    ck("a MIXED leg grades unknown, not sentinel",
       leg_scale(pk, "mixed")[0] == SCALE_UNKNOWN)
    ck("a mixed leg still reports its sentinel count",
       leg_scale(pk, "mixed")[2] == 1)
    ck("an ungradeable leg is unknown", leg_scale(pk, "blind")[0] == SCALE_UNKNOWN)
    ck("a leg with no packages is unknown", leg_scale(pk, "absent")[0] == SCALE_UNKNOWN)

    # --- grade_block: the refusal without --packages ------------------------
    block = {
        "totalTrades": 4, "totalPnl": 10.0, "totalR": 2.0, "expectancyR": 0.5,
        "rCoverage": 1.0,
        "rBasis": {"declaredInitial": 2, "storedStop": 2,
                   "refusedWrongSide": 0, "noBasis": 0},
        "perStrategy": [
            {"name": "scalp", "trades": 2, "totalPnl": 10.0, "totalR": 2.0,
             "expectancyR": 1.0,
             "rBasis": {"declaredInitial": 2, "storedStop": 0}},
            {"name": "pairs_a", "trades": 2, "totalPnl": 0.0, "totalR": 0.0,
             "expectancyR": 0.0,
             "rBasis": {"declaredInitial": 0, "storedStop": 2}},
        ],
    }
    nb = grade_block(block, None)
    ck("no packages -> scale_graded False", nb["scale_graded"] is False)
    ck("no packages -> NO admissible share published", nb["admissible"] is None)
    ck("no packages -> no restated aggregate", nb["restated"] is None)
    ck("basis census still reported without packages",
       nb["basis"][BASIS_STORED] == 2)
    ck("reconciliation runs without packages", nb["reconciles"]["ok"] is True)

    wb = grade_block(block, pk)
    ck("with packages -> admissible share published",
       wb["admissible"]["admissible_share"] == 0.5)
    ck("the sentinel leg is inadmissible",
       wb["per_leg"]["pairs_a"]["admissible"] is False)
    ck("the risk leg is admissible",
       wb["per_leg"]["scalp"]["admissible"] is True)
    ck("inadmissible share of the block is counted",
       wb["admissible"]["inadmissible_share_of_block"] == 0.5)
    ck("the restated aggregate is computed over admissible rows only",
       wb["restated"]["expectancy_r_admissible_only"] == 1.0)
    ck("the reported aggregate is kept beside it",
       wb["restated"]["expectancy_r_as_reported"] == 0.5)
    ck("the ratio is reported", wb["restated"]["ratio"] == 2.0)
    ck("the dilution is visible in |PnL| share too",
       wb["admissible"]["inadmissible_share_of_abs_pnl"] == 0.0)

    # --- grade_block: the reconciliation must FAIL on a doctored block ------
    bad = json.loads(json.dumps(block))
    bad["totalTrades"] = 9
    ck("a per-leg table that does not sum to its block is caught",
       grade_block(bad, pk)["reconciles"]["ok"] is False)
    bad2 = json.loads(json.dumps(block))
    bad2["totalR"] = 99.0
    ck("a totalR that does not sum is caught too",
       grade_block(bad2, pk)["reconciles"]["ok"] is False)

    # --- the bound is STATED, never assumed ---------------------------------
    ck("max |expectancyR| is reported with its leg",
       grade_block(block, pk)["max_abs_expectancy_r_leg"] == "scalp")
    ck("max |expectancyR| picks the largest ABSOLUTE value",
       grade_block({"totalTrades": 2, "totalR": 0.0, "perStrategy": [
           {"name": "a", "trades": 1, "totalR": 0.0, "totalPnl": 0.0,
            "expectancyR": 0.5},
           {"name": "b", "trades": 1, "totalR": 0.0, "totalPnl": 0.0,
            "expectancyR": -3.0}]}, pk)["max_abs_expectancy_r"] == -3.0)

    # --- a non-mapping block is refused, not graded -------------------------
    ck("a non-mapping block is refused",
       grade_block(None, pk)["reported"].get("error") is not None)

    # --- render never raises ------------------------------------------------
    for r_ in (nb, wb, grade_block(bad, pk), grade_block(None, pk)):
        try:
            render(r_, bound=3.0)
        except Exception as exc:  # noqa: BLE001  # allow-silent: this IS the assertion — the control is that render() raises for NO reachable report, so every catch becomes a named FAIL rather than a swallowed error.
            fails.append(f"render raised: {exc}")

    total = ran[0]
    print(f"self-test: {total - len(fails)}/{total} PASS")
    for f in fails:
        print("  FAIL:", f)
    return 1 if fails else 0


def _load(path):
    with open(path) as fh:
        d = json.load(fh)
    if isinstance(d, dict):
        for k in ("rows", "items"):
            if isinstance(d.get(k), list):
                return d[k]
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--performance", help="a /api/bot/performance payload (JSON)")
    ap.add_argument("--packages",
                    help="a /api/diag/journal?table=order_packages pull — without "
                         "it the scale axis is refused, not assumed")
    ap.add_argument("--sentinel-frac", type=float, default=DEFAULT_SENTINEL_FRAC)
    ap.add_argument("--bound", type=float, default=None,
                    help="a STATED plausible bound for |expectancyR| "
                         "(PB-20260821's clause 1 requires one to be stated)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()
    if not a.performance:
        ap.error("--performance is required")

    with open(a.performance) as fh:
        perf = json.load(fh)
    pkgs = _load(a.packages) if a.packages else None

    blocks = [("real_money", perf)]
    for k in ("demo", "paper", "paperPortfolio"):
        if isinstance(perf.get(k), dict):
            blocks.append((k, perf[k]))

    out = {"window": perf.get("window"), "since": perf.get("since"),
           "sentinel_frac": a.sentinel_frac, "stated_bound": a.bound,
           "blocks": {}}
    for label, b in blocks:
        out["blocks"][label] = grade_block(
            b, pkgs, sentinel_frac=a.sentinel_frac, label=label)

    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"window={out['window']} since={out['since']} "
              f"sentinel_frac={a.sentinel_frac} stated_bound={a.bound}")
        for label in out["blocks"]:
            print()
            print(render(out["blocks"][label], bound=a.bound))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
