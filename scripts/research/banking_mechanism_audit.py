#!/usr/bin/env python3
"""What actually banks R on the live fleet — per leg, over a stated population (MI-163).

The *target* half of M20's exit thesis closed on 2026-09-07 (MI-146→158). This
module measures the **banking** half: *does anything move the stop up to bank R
as it accrues, and if so, how much excursion does that take?*

⚠️ **`src/runtime/exit_plan.py` IS NOT ON THE LIVE EXIT PATH** and this module
deliberately does not read it. Both of its callers write to journal columns /
a soak log and nothing reads either back to drive an order — established by
tracing readers, not by reading the docstring (see ``--why-shadow``). Grading
the fleet's banking behaviour from `exit_plan`'s levers would describe a shadow
artifact. The live banking behaviour is whatever the STRATEGY UNIT's
``monitor()`` returns as ``{"sl": ...}``, applied by
``order_monitor._apply_update``.

Two live banking mechanisms exist, and they are NOT the same thing:

* **CHANDELIER RATCHET** (``trend_donchian`` · ``htf_pullback_trend_2h`` ·
  ``squeeze_breakout_4h``) — each tick proposes ``extreme ∓ trail_mult × ATR``
  from the since-entry extreme, returned ONLY when it tightens. Monotone: the
  stop never loosens. This one genuinely banks R, and how much excursion that
  costs is pure arithmetic in the leg's own declared params:

      1R           = atr_stop_mult × ATR          (the entry stop distance)
      stop == entry  when  extreme − trail_mult×ATR == entry
      ⇒ R_TO_BREAKEVEN = trail_mult / atr_stop_mult
      ⇒ excursion needed to bank +kR = R_TO_BREAKEVEN + k

* **ONE-SHOT BREAK-EVEN** (``ict_scalp`` · ``turtle_soup`` · ``vwap`` via
  ``_base.monitor_breakeven_sl``) — fires once when price reaches
  ``be_at_r × 1R`` AND ``sl < entry``, moving the stop to ``entry ± be_offset``.
  Once ``sl >= entry`` the guard ``sl < entry`` is False forever, so it NEVER
  fires again. It banks break-even plus a few bps. **It does not accrue R.**

⚠️ **OVER THE 44 ENABLED+LIVE LEGS, `be_at_r` IS UNREACHABLE.** All 8 one-shot
legs are ``ict_scalp``, and ``ict_scalp`` NEVER reads ``be_at_r`` — it calls
``monitor_breakeven_sl(open_pkg, candles_df, be_offset_bps=...)`` with no
``one_r_threshold``, so the threshold is ``_base``'s signature default of 1.0.
The two units that DO read it (``turtle_soup.py:550``, ``vwap.py:1127``) are
`execution: shadow` and `enabled: false` respectively — neither is in the
population. So declaring `be_at_r` on any of the 44 would change NOTHING.
"0 of 44 declare it" is a statement about YAML; the live fact is that the lever
is inert, which is the same code-default-supplies-what-YAML-omits shape MI-156
found for `tp_r`.

Tier-1: measurement only. Proposes nothing, touches no config, arms nothing.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

STRATEGIES_YAML = os.path.join(_REPO, "config", "strategies.yaml")

# Monitor unit -> banking mechanism. Derived by READING each unit's monitor(),
# not declared: the ratchet units return {"sl": candidate} only under a
# tightening comparison; the one-shot units delegate to monitor_breakeven_sl.
RATCHET_UNITS = {"trend_donchian", "htf_pullback_trend_2h", "squeeze_breakout_4h",
                 "fade_breakout_4h"}
ONESHOT_BE_UNITS = {"ict_scalp", "turtle_soup", "vwap"}

# The repo's own precedent floor for a live-n comparison
# (scripts/research/backtest_fidelity_calibrate.py::MIN_LIVE_N). Adopted by
# MI-155 for the per-leg verdict; reused here for the POOLED verdict.
MIN_LIVE_N = 30

# Defaults each ratchet unit falls back to when neither meta nor cfg declares.
UNIT_DEFAULTS = {
    "trend_donchian": {"atr_stop_mult": 2.5, "trail_mult": 3.0},
    "htf_pullback_trend_2h": {"atr_stop_mult": 2.5, "trail_mult": 3.0},
    "squeeze_breakout_4h": {"atr_stop_mult": 2.0, "trail_mult": 3.5},
    "fade_breakout_4h": {"atr_stop_mult": 2.0, "trail_mult": 3.5},
}

LEVERS = ["trail_mult", "tp_r", "trail_decay_tight_mult", "trail_decay_stall_bars",
          "be_offset_bps", "trail_decay_arm_r", "stale_exit_below_r", "giveback_r",
          "be_at_r"]

# Monitor units that carry the `record_position_telemetry` hook. A leg whose unit
# is NOT here produces NO peak-R telemetry — so a zero row-count for it is
# **we did not look**, never "it did not trade". Derived by grepping the units,
# and re-derived at runtime by `telemetry_hook_gap()` so this list cannot go
# stale silently.
TELEMETRY_HOOKED_UNITS = {"trend_donchian", "htf_pullback_trend_2h"}


def _f(v) -> Optional[float]:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def load_population() -> Dict[str, dict]:
    """The 44 enabled+live legs: `enabled: true` AND `execution: live`.

    Reproduces MI-146 / MI-148 / MI-155's denominator exactly.
    """
    import yaml
    with open(STRATEGIES_YAML) as fh:
        doc = yaml.safe_load(fh)
    strategies = doc.get("strategies", doc) if isinstance(doc, dict) else {}
    return {
        name: cfg for name, cfg in strategies.items()
        if isinstance(cfg, dict)
        and cfg.get("enabled") is True
        and str(cfg.get("execution", "live")).lower() == "live"
    }


def resolve_monitor_unit(name: str) -> str:
    from src.runtime.pipeline import monitor_unit_for
    return monitor_unit_for(name)


def grade_leg(name: str, cfg: dict) -> dict:
    """Per-leg banking verdict. Arithmetic only — no fitting, no estimation."""
    unit = resolve_monitor_unit(name)
    row: Dict[str, Any] = {
        "leg": name,
        "timeframe": cfg.get("timeframe"),
        "monitor_unit": unit,
        "declared": {k: cfg.get(k) for k in LEVERS if cfg.get(k) is not None},
    }

    if unit in RATCHET_UNITS:
        row["mechanism"] = "chandelier_ratchet"
        row["monotone"] = True
        d = UNIT_DEFAULTS.get(unit, {})
        stop_mult = _f(cfg.get("atr_stop_mult"))
        trail_mult = _f(cfg.get("trail_mult"))
        row["atr_stop_mult"] = stop_mult if stop_mult is not None else d.get("atr_stop_mult")
        row["trail_mult"] = trail_mult if trail_mult is not None else d.get("trail_mult")
        row["atr_stop_mult_source"] = "declared" if stop_mult is not None else "code_default"
        row["trail_mult_source"] = "declared" if trail_mult is not None else "code_default"
        sm, tm = row["atr_stop_mult"], row["trail_mult"]
        if sm and tm and sm > 0:
            row["r_to_breakeven"] = round(tm / sm, 4)
            row["r_to_bank_1r"] = round(tm / sm + 1.0, 4)
            # The tightened mult, if the decay lever is declared AND armed.
            tight = _f(cfg.get("trail_decay_tight_mult"))
            if tight is not None and tight > 0:
                row["r_to_breakeven_decayed"] = round(tight / sm, 4)
                row["decay_arm_r"] = _f(cfg.get("trail_decay_arm_r"))
        else:
            row["r_to_breakeven"] = None
            row["r_to_bank_1r"] = None
            row["ungradeable_reason"] = "atr_stop_mult or trail_mult unresolvable"
    elif unit in ONESHOT_BE_UNITS:
        row["mechanism"] = "one_shot_breakeven"
        row["monotone"] = True   # it only ever fires once, upward
        row["accrues_r"] = False
        be = _f(cfg.get("be_at_r"))
        row["be_at_r"] = be if be is not None else 1.0
        row["be_at_r_source"] = "declared" if be is not None else "code_default_1.0"
        row["be_offset_bps"] = _f(cfg.get("be_offset_bps")) or 0.0
        # Banks break-even + offset, never more. Expressed in R for comparability.
        row["r_banked_ceiling"] = 0.0
    else:
        row["mechanism"] = "unknown_unit"
        row["monotone"] = None
        row["ungradeable_reason"] = f"monitor unit {unit!r} not classified — WE DID NOT LOOK"
    return row


def telemetry_hook_gap(legs: Dict[str, dict]) -> dict:
    """Which legs are STRUCTURALLY INVISIBLE to peak-R telemetry, and why.

    ``record_position_telemetry`` is called from inside a strategy unit's
    ``monitor()``. Only ``trend_donchian`` and ``htf_pullback_trend_2h`` carry
    it. A leg on any other unit therefore emits **no** telemetry row however
    much it trades — so its ``n_live = 0`` is *we did not look*, and grading it
    with a genuine zero-volume leg collapses two different facts.

    ⚠️ MI-155's verdict table grades all 13 zero-n legs identically as
    ``insufficient_n``. Measured here, those 13 split **9 invisible / 4 real**.

    The hooked set is re-derived from the source rather than trusted, so this
    detector cannot silently rot when a unit gains or loses the hook.
    """
    hooked = set()
    units = {g["monitor_unit"] for g in legs.values()} if legs else set()
    for unit in sorted(units):
        path = os.path.join(_REPO, "src", "units", "strategies", f"{unit}.py")
        try:
            with open(path) as fh:
                if "record_position_telemetry" in fh.read():
                    hooked.add(unit)
        except OSError:
            continue
    invisible = sorted(n for n, g in legs.items()
                       if g["monitor_unit"] not in hooked)
    return {
        "hooked_units": sorted(hooked),
        "declared_hooked_units": sorted(TELEMETRY_HOOKED_UNITS),
        "drifted": sorted(hooked) != sorted(TELEMETRY_HOOKED_UNITS),
        "legs_structurally_invisible": invisible,
        "n_invisible": len(invisible),
        "n_population": len(legs),
    }


def counterfactual(rows: List[dict], legs: Dict[str, dict],
                   thresholds: List[float]) -> dict:
    """What a single fleet-wide "park the stop at +X once peak_r >= X" would have done.

    Restricted to **closed** rows, where ``open_r`` is the terminal R. A row with
    ``peak_r >= X`` and terminal ``< X`` must have crossed X on the way down, so
    such a stop would have exited at ``>= +X``: the improvement ``X - open_r`` is
    a rigorous LOWER BOUND.

    ⚠️ **THIS IS NOT A NET RESULT AND MUST NOT BE QUOTED AS ONE.** The telemetry
    carries the peak and the terminal, never the PATH, so the forgone-continuation
    cost — a stop at +X exits on the FIRST retrace to X, on a trade that may have
    resumed afterwards — is **not measurable from this corpus**. Establishing net
    needs the path-aware harness (`src/research/trail_levers.py`), not this file.
    """
    closed = [r for r in rows
              if r.get("lifecycle") == "closed" and r.get("peak_gradeable") is True
              and _f(r.get("peak_r")) is not None and _f(r.get("open_r")) is not None
              and r.get("strategy") in legs]
    if not closed:
        return {"n": 0, "verdict": "no_closed_gradeable_rows_in_population"}
    actual = sum(float(r["open_r"]) for r in closed)
    out = {
        "population": "closed + peak_gradeable + strategy in the 44",
        "n": len(closed),
        "actual_total_r": round(actual, 4),
        "actual_mean_r_per_trade": round(actual / len(closed), 4),
        "is_net": False,
        "cost_side": "UNMEASURED — telemetry carries peak+terminal, never the path",
        "arms": [],
    }
    for x in thresholds:
        armed = [r for r in closed if float(r["peak_r"]) >= x]
        rescued = [r for r in armed if float(r["open_r"]) < x]
        delta = sum(x - float(r["open_r"]) for r in rescued)
        out["arms"].append({
            "threshold_r": x, "would_arm": len(armed), "rescued": len(rescued),
            "gross_delta_r_lower_bound": round(delta, 4),
            "mean_r_per_trade_lower_bound": round((actual + delta) / len(closed), 4),
        })
    return out


def load_telemetry(path: str) -> List[dict]:
    with open(path) as fh:
        doc = json.load(fh)
    return doc.get("rows", [])


def pooled_analysis(rows: List[dict], legs: Dict[str, dict],
                    thresholds: List[float]) -> dict:
    """What a SINGLE fleet-wide 'bank at +XR' threshold would have reached.

    ⚠️ EVERY figure here is a LOWER BOUND: `peak_r_is_lower_bound` is true on
    every row the telemetry serves, so a row counted as 'did not reach X' may
    have reached X between the samples. Under-counting is the only direction
    the error runs, so a reach-rate reported here is a FLOOR on the true rate.
    """
    gradeable = [r for r in rows if r.get("peak_gradeable") is True
                 and _f(r.get("peak_r")) is not None]
    out: Dict[str, Any] = {
        "rows_returned": len(rows),
        "gradeable": len(gradeable),
        "excluded_not_gradeable": len(rows) - len(gradeable),
        "all_peak_r_lower_bound": all(r.get("peak_r_is_lower_bound") is True for r in rows),
        "pooled_n_vs_floor": {"n": len(gradeable), "floor": MIN_LIVE_N,
                              "verdict": "sufficient" if len(gradeable) >= MIN_LIVE_N
                                         else "insufficient_n"},
        "lifecycle": dict(Counter(r.get("lifecycle") for r in gradeable)),
        "in_population": sum(1 for r in gradeable if r.get("strategy") in legs),
        "outside_population": sum(1 for r in gradeable if r.get("strategy") not in legs),
    }

    # Horizon composition — MI-155's finding is that p90 moves 4.5x across
    # timeframes, so a pooled threshold is confounded unless this is shown.
    by_tf: Dict[Any, List[float]] = defaultdict(list)
    for r in gradeable:
        cfg = legs.get(r.get("strategy"))
        tf = cfg.get("timeframe") if cfg else "not_in_population"
        by_tf[tf].append(float(r["peak_r"]))
    out["by_timeframe"] = {
        str(tf): {"n": len(v), "median_peak_r": round(sorted(v)[len(v) // 2], 3),
                  "max_peak_r": round(max(v), 3)}
        for tf, v in sorted(by_tf.items(), key=lambda kv: -len(kv[1]))
    }

    peaks = [float(r["peak_r"]) for r in gradeable]
    out["reach_rates"] = []
    for x in thresholds:
        hit = sum(1 for p in peaks if p >= x)
        out["reach_rates"].append({
            "threshold_r": x,
            "reached_at_least": hit,
            "of_n": len(peaks),
            "reach_rate_lower_bound": round(hit / len(peaks), 4) if peaks else None,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--telemetry", help="position_telemetry JSON (from "
                                        "/api/diag/position_telemetry?limit=1000)")
    ap.add_argument("--thresholds", default="0.5,1.0,1.5,2.0,3.0",
                    help="fleet-wide bank-at-+XR thresholds to evaluate")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    legs = load_population()
    graded = [grade_leg(n, c) for n, c in sorted(legs.items())]

    coverage = {lev: sum(1 for c in legs.values() if c.get(lev) is not None)
                for lev in LEVERS}

    report: Dict[str, Any] = {
        "population": {
            "definition": "enabled: true AND execution: live in config/strategies.yaml",
            "n": len(legs),
        },
        "exit_plan_is_live": False,
        "exit_plan_basis": (
            "src/core/coordinator.py:3483-3526 writes exit_plan/exit_plan_state into "
            "order_packages columns; src/runtime/exit_ladder_soak.py writes a JSONL soak. "
            "No reader of either column drives an order — the only src/ references are "
            "database.py:1425-1428, which JSON-serialise on the WRITE path."
        ),
        "lever_coverage_over_population": coverage,
        "mechanism_census": dict(Counter(g["mechanism"] for g in graded)),
        "legs": graded,
    }

    report["telemetry_hook_gap"] = telemetry_hook_gap(
        {g["leg"]: g for g in graded})

    if args.telemetry:
        tele = load_telemetry(args.telemetry)
        report["counterfactual"] = counterfactual(
            tele, legs, [float(x) for x in args.thresholds.split(",")])
        report["pooled"] = pooled_analysis(
            tele, legs, [float(x) for x in args.thresholds.split(",")])

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    P = report["population"]
    print(f"POPULATION: {P['n']} legs — {P['definition']}\n")
    print(f"exit_plan.py on the live exit path? {report['exit_plan_is_live']}")
    print(f"  basis: {report['exit_plan_basis']}\n")
    print("LEVER COVERAGE (declared in YAML, over the population):")
    for lev, n in coverage.items():
        print(f"  {lev:26s} {n:2d} / {P['n']}")
    print("\nBANKING MECHANISM CENSUS:")
    for m, n in report["mechanism_census"].items():
        print(f"  {m:22s} {n:2d}")
    print(f"\n{'leg':30s} {'tf':4s} {'mechanism':20s} {'R→BE':>7s} {'R→+1R':>7s}")
    print("-" * 76)
    for g in graded:
        print(f"{g['leg']:30s} {str(g.get('timeframe') or '-'):4s} "
              f"{g['mechanism']:20s} "
              f"{('%.2f' % g['r_to_breakeven']) if g.get('r_to_breakeven') else '—':>7s} "
              f"{('%.2f' % g['r_to_bank_1r']) if g.get('r_to_bank_1r') else '—':>7s}")
    hg = report["telemetry_hook_gap"]
    print(f"\nTELEMETRY HOOK GAP: {hg['n_invisible']} / {hg['n_population']} legs emit NO "
          f"peak-R telemetry (structurally invisible — 'we did not look', never 'no trades')")
    print(f"  hooked units: {hg['hooked_units']}")
    if hg["drifted"]:
        print("  ⚠️ the hooked set has DRIFTED from this module's declared constant")
    for n in hg["legs_structurally_invisible"]:
        print(f"    {n}")

    if "counterfactual" in report and report["counterfactual"].get("n"):
        c = report["counterfactual"]
        print(f"\nCOUNTERFACTUAL — fleet-wide stop parked at +X once peak_r >= X")
        print(f"  POPULATION: {c['population']}, n={c['n']}")
        print(f"  actual realised {c['actual_total_r']:+.2f}R "
              f"({c['actual_mean_r_per_trade']:+.3f}R/trade)")
        print(f"  ⚠️ NOT NET — {c['cost_side']}")
        print(f"    {'X':>5s} {'arms':>5s} {'rescued':>8s} {'grossΔR(>=)':>12s} {'mean/trade':>11s}")
        for a in c["arms"]:
            print(f"    {a['threshold_r']:5.2f} {a['would_arm']:5d} {a['rescued']:8d} "
                  f"{a['gross_delta_r_lower_bound']:+12.2f} "
                  f"{a['mean_r_per_trade_lower_bound']:+11.3f}")

    if "pooled" in report:
        p = report["pooled"]
        print(f"\nPOOLED CORPUS: {p['gradeable']} gradeable of {p['rows_returned']} returned")
        print(f"  n vs floor {p['pooled_n_vs_floor']['floor']}: "
              f"{p['pooled_n_vs_floor']['verdict']}")
        print(f"  every peak_r is a LOWER BOUND: {p['all_peak_r_lower_bound']}")
        print(f"  lifecycle: {p['lifecycle']}")
        print("  reach rates (LOWER BOUNDS):")
        for rr in p["reach_rates"]:
            print(f"    peak_r >= {rr['threshold_r']:>4}R : {rr['reached_at_least']:3d}"
                  f" / {rr['of_n']}  ({rr['reach_rate_lower_bound']:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
