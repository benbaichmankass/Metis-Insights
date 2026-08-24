#!/usr/bin/env python3
"""Does ONE `pullback_frac` generalise across the fleet, or is each value fit
to its own leg?

Operator-approved 2026-08-24 at FULL scope: all 19 enabled legs, two strata.

WHY THIS EXISTS
---------------
19 enabled legs declare `pullback_frac` at TWO different values (0.5 x11,
0.618 x8; see `pullback_frac_cross_leg_scope.py`, which owns the population and
is imported here rather than re-derived). Neither value is universal today, and
nothing has ever measured whether either SHOULD be. That is a cross-leg claim,
so the first thing that can invalidate it is a population that quietly excludes
legs -- and 12 of the 19 are exactly the symbols that had no free candle lane at
all until 2026-08-24.

⚠️ THIS IS A SWEEP DRIVER, NOT A NEW ENGINE. `scripts/backtest_pullback.py`
already accepts `--pullback-frac`, and `m20_fleet_exit_sweep.base_args` already
threads the leg's CONFIGURED value into it (`opt("--pullback-frac",
"pullback_frac")`), so a leg's base is config-exact on this axis. Verified by
reading both, not assumed. Every resolver here -- `classify`, `resolve_data`,
`base_args`, `run_cell`, `FAMILY_HARNESS` -- is IMPORTED from the fleet sweep.
Re-deriving any of them is how this tool and the fleet sweep would come to
disagree about what a leg's base is.

⚠️⚠️ THE CRITERION GOES FIRST, AND IT IS WRITTEN DOWN BELOW BEFORE ANY RESULT
EXISTS. This is the donchian section 6.0b lesson: a shortlist chosen after the
candidates are measured is a shortlist chosen by the argmax. `VERDICT_RULE`
and `MIN_LEGS_FOR_A_CROSS_LEG_CLAIM` are the gate, and they are constants in
this file rather than a paragraph in the eventual report.

⚠️ SPAN IS PART OF THE POPULATION AND THE STRATA ARE NEVER BLENDED. yfinance
serves 1d uncapped but REFUSES a >730 d 1h request outright (measured, proof
run 32734360738 -- it returns ZERO rows, it does not truncate). So a decade-long
daily leg and a two-year hourly leg do not share a denominator, and "N legs
agree" across both would be an unstated-population claim -- the exact error
`docs/CLAUDE-RULES-CANONICAL.md` section "Always state the population" names.
`summarise` refuses to emit a blended count; there is no flag to make it.

⚠️ NET OF FEES IS LOAD-BEARING, for the same reason it is on the bracket sweep.
`pullback_frac` is an ENTRY gate: moving it changes which pullbacks qualify, so
it changes the trade POPULATION and the turnover -- the per-exit fee does NOT
cancel between arms the way it does in a lever replay. A fee-free basis would
flatter a looser frac exactly where it is most likely to pass. Every run charges
the harness's own `--fee-bps-roundtrip`.

Observe-only, Tier-1: reads config + candles, writes a report, touches nothing
live. Changing `pullback_frac` in `config/strategies.yaml` is **Tier-3** and is
NOT what this produces -- it produces the evidence for that decision.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

from research import m20_fleet_exit_sweep as fleet  # noqa: E402
from research import pullback_frac_cross_leg_scope as scope_mod  # noqa: E402
from src.runtime.execution_costs import DEFAULT_FEE_BPS_ROUNDTRIP  # noqa: E402

# ---------------------------------------------------------------------------
# THE GRID. Every value has a REASON -- none is a round number picked to widen
# the axis, because an unmotivated grid point is how an argmax finds noise.
#   0.33   the `htf_pullback_trend_2h` UNIT's own default (its module-level
#          DEFAULTS block), i.e. a value the codebase already considered live.
#   0.5    a live config value (11 legs).
#   0.618  a live config value (8 legs).
#   0.75   a SHAPE probe on the far side of both live values. Present so the
#          surface can be seen rather than a two-point comparison being read as
#          a slope -- two points cannot distinguish "0.618 is better" from
#          "the surface is flat and noise picked one".
# ---------------------------------------------------------------------------
FRAC_GRID = (0.33, 0.5, 0.618, 0.75)

# ---------------------------------------------------------------------------
# THE CRITERION -- fixed before any result exists.
# ---------------------------------------------------------------------------
# A value "generalises" only if it is the best value on a MAJORITY of the legs
# in its stratum AND that majority is more than a coin flip could produce. With
# a 4-value grid, a value wins a leg by chance 1/4 of the time, so "best on more
# legs than any other" is a weak claim on a small stratum. The bar is therefore
# stated as a FRACTION of the stratum, and a stratum below the floor is reported
# as UNDERPOWERED rather than given a winner.
VERDICT_RULE = (
    "a value GENERALISES within a stratum iff it is the argmax net_R on "
    "> 50% of that stratum's legs; below that the stratum is reported "
    "SPLIT. A stratum with fewer than MIN_LEGS_FOR_A_CROSS_LEG_CLAIM legs "
    "is UNDERPOWERED and gets no verdict at all."
)
MIN_LEGS_FOR_A_CROSS_LEG_CLAIM = 5

# The spread headline. Same reasoning as the bracket sweep: if net_R barely
# moves across the grid, the parameter has little to give and the per-leg
# argmax is noise wearing a recommendation's clothes. Reported ALWAYS, and
# before the argmax, so a flat surface cannot be read as a winner.
FLAT_SURFACE_R = 1.0


def _f(row: dict, key: str) -> Optional[float]:
    v = row.get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def plan_legs(data_dir: Path, only: Optional[List[str]]) -> tuple[list, list]:
    """(runnable, skipped) -- population from the SCOPE tool, data/base from the
    FLEET sweep. Neither is re-derived here."""
    import yaml
    cfg_all = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
    strats = cfg_all.get("strategies", cfg_all)
    yf = scope_mod._yf_symbols()
    runnable, skipped = [], []
    for row in scope_mod.scope(strats, yf):
        name = row["leg"]
        if only and name not in only:
            continue
        if row["lane"] == "NOT_SERVED":
            # WE CANNOT TEST IT -- never folded in as a disagreement.
            skipped.append({"leg": name, "reason": f"not_served:{row['reason']}"})
            continue
        c = strats[name]
        fam = fleet.classify(name)
        if fam is None:
            skipped.append({"leg": name, "reason": "family_unresolved"})
            continue
        sym, tf = row["symbol"], row["timeframe"]
        data, resample, proxy = fleet.resolve_data(sym, tf, data_dir)
        if data is None:
            # Distinct from `not_served`: the LANE exists, the local CSV does
            # not. Different fix, so a different state.
            skipped.append({"leg": name, "reason": f"data_missing:{sym}"})
            continue
        runnable.append({
            "leg": name, "family": fam, "symbol": sym, "tf": tf,
            "harness": fleet.FAMILY_HARNESS[fam], "data": data,
            "resample": resample, "proxy": proxy,
            "declared_frac": float(c["pullback_frac"]),
            "stratum": "full" if row["span"] == "full" else f"capped_{row['span']}",
            "base": fleet.base_args(name, c, fam, data, resample, 0.0,
                                    fee_bps_roundtrip=DEFAULT_FEE_BPS_ROUNDTRIP),
        })
    return runnable, skipped


# Leg timeframe -> the interval code `scripts/ops/fetch_backtest_candles.py`
# wants. Deliberately NOT a `.get(tf, default)`: a wrong interval does not
# error, it silently measures a different population.
TF_TO_FETCH_INTERVAL = {
    "1m": "1", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "1d": "D",
}


def plan_shards(only: Optional[List[str]]) -> tuple[list, list]:
    """(include, refused) -- one CI job per leg, planned from CONFIG ALONE.

    ⚠️ THIS DELIBERATELY DOES **NOT** CHECK THAT THE DATA EXISTS, and that is
    the difference between this planner and `e35_shard_plan.py`.

    MEASURED 2026-08-24, not theorised: leg CSVs are gitignored (`.gitignore`
    line 73, `data/*.csv`), so a fresh CI checkout has none. `e35_shard_plan`
    resolves through `plan_legs`, which requires the CSV to be on disk -- but on
    that workflow the CSV is fetched by the *per-leg job the planner is supposed
    to schedule*. Run against an empty data dir it returns
    `0 job(s); 55 not scheduled (data_missing=43, out_of_scope_family=12)` and
    exits 1. `e35-bracket-sweep.yml` has **never run** (0 workflow runs), so the
    deadlock was never discovered.

    Its refusal is the RIGHT behaviour (an empty matrix is a green run that
    tested nothing), so the bug is not the refusal -- it is planning on a
    precondition the plan itself creates. Planning from config removes the
    ordering dependency entirely. The population is still the SCOPE tool's, so
    this planner and the sweep cannot disagree about which legs exist; the only
    filter `plan_legs` adds is data presence, which is exactly what the per-leg
    job's fetch step resolves (and asserts a row floor on, so a silent-empty
    fetch fails loudly rather than reading as `data_missing`).
    """
    import yaml
    cfg_all = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
    strats = cfg_all.get("strategies", cfg_all)
    yf = scope_mod._yf_symbols()
    include, refused = [], []
    for row in scope_mod.scope(strats, yf):
        name = row["leg"]
        if only and name not in only:
            continue
        if row["lane"] == "NOT_SERVED":
            refused.append({"leg": name, "reason": f"not_served:{row['reason']}"})
            continue
        iv = TF_TO_FETCH_INTERVAL.get(str(row["timeframe"]))
        if iv is None:
            refused.append({"leg": name,
                            "reason": f"unmapped_timeframe:{row['timeframe']}"})
            continue
        sym = row["symbol"]
        include.append({
            "leg": name, "symbol": sym, "tf": row["timeframe"],
            "fetch_interval": iv,
            # The lane decides which feed the job pins. Crypto goes to
            # Binance's public archive because GitHub runners are US Azure IPs
            # and Bybit geoblocks the US (BL-20260727-BYBIT-USGEOBLOCK-GHRUNNER).
            "feed_source": "binance_vision" if row["lane"] == "crypto_archive"
                           else "yfinance",
            "stratum": "full" if row["span"] == "full" else f"capped_{row['span']}",
        })
    return include, refused


def cell_args(base: list[str], frac: float) -> list[str]:
    """Replace --pullback-frac in the config-exact base.

    REPLACE, never append: argparse takes the LAST occurrence, so appending
    would appear to work while leaving the base's own value in the argv --
    invisible in the result and wrong in any log of the invocation.
    """
    out, i = [], 0
    replaced = False
    while i < len(base):
        if base[i] == "--pullback-frac":
            out += ["--pullback-frac", str(frac)]
            i += 2
            replaced = True
            continue
        out.append(base[i])
        i += 1
    if not replaced:
        out += ["--pullback-frac", str(frac)]
    return out


def sweep_leg(leg: dict, log=print) -> dict:
    harness, base = leg["harness"], leg["base"]
    base_row = fleet.run_cell(harness, base)
    if "error" in base_row:
        return {"leg": leg["leg"], "error": base_row["error"]}
    b_net = _f(base_row, "net_total_r")
    cells = []
    for frac in FRAC_GRID:
        if abs(frac - leg["declared_frac"]) < 1e-9:
            # A grid point equal to the leg's own config value moves nothing.
            # Recorded, NEVER run -- a measured 0.0 delta here would be
            # indistinguishable from a value that ran and did nothing. Same
            # rule as `e35_bracket_geometry_sweep`'s `inert_equals_base`.
            cells.append({"frac": frac, "state": "inert_equals_base",
                          "net_total_r": b_net, "d_net_r": 0.0,
                          "is_declared": True})
            continue
        r = fleet.run_cell(harness, cell_args(base, frac))
        if "error" in r:
            cells.append({"frac": frac, "state": "error",
                          "error": r["error"], "is_declared": False})
            continue
        n = _f(r, "net_total_r")
        cells.append({
            "frac": frac, "state": "measured", "net_total_r": n,
            "d_net_r": None if (n is None or b_net is None) else round(n - b_net, 4),
            "trades": r.get("total_trades"),
            "max_drawdown_r": _f(r, "max_drawdown_r"),
            "is_declared": False,
        })
        log(f"    frac {frac:<5} net_R {n}")
    return {
        "leg": leg["leg"], "symbol": leg["symbol"], "tf": leg["tf"],
        "stratum": leg["stratum"], "declared_frac": leg["declared_frac"],
        "proxy": leg["proxy"],
        "base": {"net_total_r": b_net, "trades": base_row.get("total_trades"),
                 "max_drawdown_r": _f(base_row, "max_drawdown_r")},
        "cells": cells,
    }


def leg_argmax(leg_result: dict) -> Optional[dict]:
    """Best grid value for ONE leg, plus the SPREAD across the grid.

    Returns None when fewer than two grid points produced a book -- an argmax
    over one point is not an argmax, and reporting it as one is how a
    single-cell leg silently becomes a vote.
    """
    usable = [c for c in leg_result.get("cells", [])
              if c.get("net_total_r") is not None
              and c.get("state") in ("measured", "inert_equals_base")]
    if len(usable) < 2:
        return None
    best = max(usable, key=lambda c: c["net_total_r"])
    nets = [c["net_total_r"] for c in usable]
    spread = max(nets) - min(nets)
    return {"best_frac": best["frac"], "best_net_r": best["net_total_r"],
            "spread_r": round(spread, 4), "graded_points": len(usable),
            "flat": spread < FLAT_SURFACE_R}


def summarise(results: List[dict]) -> Dict[str, Any]:
    """Per-stratum ONLY. There is deliberately no all-legs roll-up."""
    strata: Dict[str, list] = {}
    ungraded = []
    for r in results:
        if r.get("error"):
            ungraded.append({"leg": r["leg"], "reason": "harness_error"})
            continue
        am = leg_argmax(r)
        if am is None:
            # NOT a disagreement and NOT a vote -- we could not grade it.
            ungraded.append({"leg": r["leg"], "reason": "under_two_graded_points"})
            continue
        strata.setdefault(r["stratum"], []).append({**am, "leg": r["leg"],
                                                    "declared": r["declared_frac"]})
    out = {}
    for name, legs in sorted(strata.items()):
        votes: Dict[float, int] = {}
        for row in legs:
            votes[row["best_frac"]] = votes.get(row["best_frac"], 0) + 1
        n = len(legs)
        top = max(votes.items(), key=lambda kv: kv[1]) if votes else None
        if n < MIN_LEGS_FOR_A_CROSS_LEG_CLAIM:
            verdict, winner = "UNDERPOWERED", None
        elif top and top[1] / n > 0.5:
            verdict, winner = "GENERALISES", top[0]
        else:
            verdict, winner = "SPLIT", None
        spreads = [row["spread_r"] for row in legs]
        out[name] = {
            "legs": n, "votes": {str(k): v for k, v in sorted(votes.items())},
            "verdict": verdict, "winner": winner,
            "flat_legs": sum(1 for row in legs if row["flat"]),
            "median_spread_r": round(statistics.median(spreads), 4),
            "max_spread_r": round(max(spreads), 4),
            "per_leg": legs,
        }
    return {"strata": out, "ungraded": ungraded,
            "criterion": VERDICT_RULE,
            "min_legs_for_a_claim": MIN_LEGS_FOR_A_CROSS_LEG_CLAIM,
            "flat_surface_r": FLAT_SURFACE_R,
            "grid": list(FRAC_GRID),
            "fee_bps_roundtrip": DEFAULT_FEE_BPS_ROUNDTRIP}


def report(summary: Dict[str, Any], out=print) -> None:
    out("pullback_frac CROSS-LEG SWEEP")
    out(f"  grid {summary['grid']} · fees {summary['fee_bps_roundtrip']} bps roundtrip")
    out(f"  CRITERION (fixed before results): {summary['criterion']}")
    out("")
    for name, s in summary["strata"].items():
        out(f"STRATUM `{name}` — {s['legs']} leg(s)")
        out(f"  argmax votes      : {s['votes']}")
        out(f"  VERDICT           : {s['verdict']}"
            + (f" -> {s['winner']}" if s["winner"] is not None else ""))
        out(f"  spread net_R      : median {s['median_spread_r']} · "
            f"max {s['max_spread_r']}")
        out(f"  legs with a FLAT surface (< {summary['flat_surface_r']} R): "
            f"{s['flat_legs']}/{s['legs']}")
        if s["flat_legs"] == s["legs"]:
            out("  ⚠️ EVERY leg in this stratum is flat. The argmax above is "
                "noise; this axis has little to give here.")
        out("")
    out("⚠️ THE STRATA ARE NOT COMBINED. A capped 730 d hourly leg and a "
        "full-history daily leg do not share a denominator, so there is no "
        "all-legs count and no flag to produce one.")
    if summary["ungraded"]:
        out(f"⚠️ UNGRADED (we could not look — never counted as disagreement): "
            f"{summary['ungraded']}")


def selftest() -> int:
    checks, failed = [], []

    def ck(label, cond):
        checks.append(label)
        if not cond:
            failed.append(label)

    # --- cell_args REPLACES rather than appends -------------------------------
    base = ["--data", "d.csv", "--pullback-frac", "0.5", "--adx-min", "20"]
    got = cell_args(base, 0.618)
    ck("frac is replaced, not appended", got.count("--pullback-frac") == 1)
    ck("the new value is present", got[got.index("--pullback-frac") + 1] == "0.618")
    ck("other flags survive", "--adx-min" in got and "20" in got)
    ck("a base with no frac flag gains one",
       cell_args(["--data", "d.csv"], 0.5).count("--pullback-frac") == 1)

    # --- the declared value is INERT, never run -------------------------------
    leg = {"leg": "L", "harness": "h", "declared_frac": 0.5, "symbol": "S",
           "tf": "1h", "stratum": "full", "proxy": None,
           "base": ["--pullback-frac", "0.5"]}
    calls = []
    real = fleet.run_cell
    try:
        # `_`-prefixed because they are genuinely unread: this stub
        # REPLACES `fleet.run_cell`, so it must accept that function's
        # signature or the call site raises, but the test asserts only
        # on the ARGV (`a`) that `cell_args` builds. Reading the harness
        # path or window bounds to look busy would be worse than saying
        # plainly that no claim here depends on them.
        def fake(_h, a, *_args, **_kwargs):
            calls.append(a)
            frac = a[a.index("--pullback-frac") + 1] if "--pullback-frac" in a else None
            return {"net_total_r": {"0.33": 1.0, "0.5": 5.0,
                                    "0.618": 9.0, "0.75": 2.0}[frac],
                    "total_trades": 10, "max_drawdown_r": 1.0}
        fleet.run_cell = fake
        res = sweep_leg(leg, log=lambda *a: None)
    finally:
        fleet.run_cell = real
    declared = [c for c in res["cells"] if c["frac"] == 0.5][0]
    ck("declared value is inert_equals_base",
       declared["state"] == "inert_equals_base")
    ck("inert cell is never run",
       not any(a[a.index("--pullback-frac") + 1] == "0.5"
               for a in calls[1:] if "--pullback-frac" in a))
    ck("inert cell carries the base net_R", declared["net_total_r"] == 5.0)
    ck("three non-declared cells ran", len(calls) == 4)  # base + 3

    am = leg_argmax(res)
    ck("argmax picks the best value", am["best_frac"] == 0.618)
    ck("spread is max-min across graded points", am["spread_r"] == 8.0)
    ck("a wide surface is not flat", am["flat"] is False)

    # --- an argmax over ONE point is refused ----------------------------------
    ck("single graded point is not an argmax",
       leg_argmax({"cells": [{"frac": 0.5, "state": "measured",
                              "net_total_r": 1.0}]}) is None)

    # --- a flat surface is FLAGGED, not silently won --------------------------
    flat = {"cells": [{"frac": f, "state": "measured", "net_total_r": v}
                      for f, v in ((0.33, 1.0), (0.5, 1.2), (0.618, 1.1))]}
    ck("a sub-threshold spread is flat", leg_argmax(flat)["flat"] is True)

    # --- STRATA ARE NEVER BLENDED --------------------------------------------
    def mk(leg, stratum, best):
        return {"leg": leg, "stratum": stratum, "declared_frac": 0.5,
                "cells": [{"frac": f, "state": "measured",
                           "net_total_r": 9.0 if f == best else 1.0}
                          for f in FRAC_GRID]}
    mixed = [mk(f"f{i}", "full", 0.618) for i in range(6)] \
        + [mk(f"c{i}", "capped_730d", 0.5) for i in range(4)]
    s = summarise(mixed)
    ck("strata are reported separately", set(s["strata"]) == {"full", "capped_730d"})
    ck("no blended all-legs roll-up exists",
       not any(k in s for k in ("all_legs", "combined", "total")))
    ck("the powered stratum gets a verdict",
       s["strata"]["full"]["verdict"] == "GENERALISES")
    ck("and names the winner", s["strata"]["full"]["winner"] == 0.618)
    # ⚠️ 4 legs is below the floor -- a majority there is NOT a claim.
    ck("an underpowered stratum gets NO verdict",
       s["strata"]["capped_730d"]["verdict"] == "UNDERPOWERED")
    ck("and no winner is named for it",
       s["strata"]["capped_730d"]["winner"] is None)

    # --- a genuinely split stratum is SPLIT, not argmax'd ---------------------
    split = [mk(f"a{i}", "full", 0.618) for i in range(3)] \
        + [mk(f"b{i}", "full", 0.5) for i in range(3)]
    ck("a tie is SPLIT, never a winner",
       summarise(split)["strata"]["full"]["verdict"] == "SPLIT")
    ck("and names no winner",
       summarise(split)["strata"]["full"]["winner"] is None)
    # An exact 50% is NOT a majority -- the rule says "> 50%".
    half = [mk(f"a{i}", "full", 0.618) for i in range(3)] \
        + [mk(f"b{i}", "full", 0.5) for i in range(2)] \
        + [mk("c0", "full", 0.75)]
    ck("exactly 50% is not a majority",
       summarise(half)["strata"]["full"]["verdict"] == "SPLIT")

    # --- ungraded legs are NOT disagreements ---------------------------------
    ung = summarise([{"leg": "x", "stratum": "full", "declared_frac": 0.5,
                      "cells": [{"frac": 0.5, "state": "measured",
                                 "net_total_r": 1.0}]}])
    ck("an ungradeable leg is recorded, not voted",
       ung["ungraded"][0]["reason"] == "under_two_graded_points")
    ck("and contributes no stratum", ung["strata"] == {})
    ck("a harness error is ungraded too",
       summarise([{"leg": "y", "error": "boom"}])["ungraded"][0]["reason"]
       == "harness_error")

    # --- the criterion is a CONSTANT, not chosen at report time ---------------
    ck("the criterion ships in the summary", "argmax" in ung["criterion"])
    ck("the floor ships too", ung["min_legs_for_a_claim"] == 5)

    # --- the shard planner is CONFIG-gated, not data-gated -------------------
    inc, ref = plan_shards(None)
    ck("the planner schedules every scoped leg with NO data present",
       len(inc) == 19)
    ck("and refuses none of them", ref == [])
    ck("crypto legs pin the Binance archive (US-geoblock on Bybit)",
       all(r["feed_source"] == "binance_vision"
           for r in inc if r["symbol"].endswith("USDT")))
    ck("non-crypto legs pin the yfinance lane",
       all(r["feed_source"] == "yfinance"
           for r in inc if not r["symbol"].endswith("USDT")))
    ck("every job carries a fetch interval",
       all(r["fetch_interval"] for r in inc))
    ck("2h maps to 120, not to a default", TF_TO_FETCH_INTERVAL["2h"] == "120")
    ck("1d maps to D", TF_TO_FETCH_INTERVAL["1d"] == "D")
    ck("an unmapped timeframe has no silent default",
       TF_TO_FETCH_INTERVAL.get("3h") is None)
    # Both strata must survive planning, or the stratification is decorative.
    ck("the planner carries both strata",
       {r["stratum"] for r in inc} == {"full", "capped_730d"})
    ck("15 full-history legs planned",
       sum(1 for r in inc if r["stratum"] == "full") == 15)
    ck("4 capped legs planned",
       sum(1 for r in inc if r["stratum"] == "capped_730d") == 4)
    ck("--only narrows the plan", len(plan_shards(["spy_pullback_1h"])[0]) == 1)

    print(f"pullback_frac cross-leg sweep selftest: {len(checks)} checks")
    for f in failed:
        print("  FAIL:", f)
    print("  all pass" if not failed else f"  {len(failed)} FAILED")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--only", help="comma-separated leg ids")
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--emit-matrix", help="write a GH Actions matrix here and exit")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    only = [s.strip() for s in a.only.split(",")] if a.only else None
    if a.emit_matrix:
        include, refused = plan_shards(only)
        for r in refused:
            print(f"  REFUSED {r['leg']}: {r['reason']}", file=sys.stderr)
        if not include:
            # An empty matrix is a GREEN RUN THAT TESTED NOTHING -- GitHub
            # reports a zero-job matrix as success, so this must be an error.
            print("::error::shard plan produced ZERO jobs. An empty matrix is a "
                  "green run that tested nothing, so this is a failure, not an "
                  f"empty success. {len(refused)} leg(s) refused.", file=sys.stderr)
            return 1
        Path(a.emit_matrix).write_text(json.dumps({"include": include}))
        print(f"shard plan: {len(include)} job(s), {len(refused)} refused",
              file=sys.stderr)
        return 0
    runnable, skipped = plan_legs(Path(a.data_dir), only)
    print(f"planned {len(runnable)} runnable leg(s), {len(skipped)} skipped")
    for s in skipped:
        print(f"  SKIP {s['leg']}: {s['reason']}")
    results = []
    for leg in runnable:
        print(f"  {leg['leg']} ({leg['symbol']} {leg['tf']}, "
              f"stratum={leg['stratum']}, declared={leg['declared_frac']})")
        results.append(sweep_leg(leg))
    summary = summarise(results)
    print("")
    report(summary)
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"summary": summary, "results": results, "skipped": skipped},
            indent=1, default=str))
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
