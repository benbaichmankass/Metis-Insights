#!/usr/bin/env python3
"""WARN when a declared `tp_r` sits above the venue ceiling it must fit under.

Operator decision 2026-08-24: **warn, do not refuse.** A target above the cap
is not invalid config — it is a target that will be silently clamped, so the
YAML reads as a decision and the book behaves as if nothing were declared.
Measured: 49 of 308 gradeable cells were byte-identical to declaring nothing,
and `trend_donchian_ada_4h` at sm1.5 returned 55.4731 at `tp_r: 6.0` and
55.4731 with no target at all.

WHY THIS IS A REPORT AND NOT A GATE
-----------------------------------
`cap_r = TP_VENUE_CAP_PCT * entry / risk` and `risk = atr_stop_mult * ATR`, so
the ceiling depends on **ATR/entry — a market quantity that does not exist at
config-load time.** This tool therefore cannot compute `cap_r` from YAML alone,
and anything claiming otherwise would be inventing the input. What it CAN do is
compare a declared `tp_r` against caps that were actually MEASURED, and, where
none exists, state the volatility a leg would need for its target to be
reachable — as a condition, never as a verdict.

Its own MECHANISM can fail (unreadable config, broken self-test) and that is an
error. Its FINDINGS never fail the build — that is the operator's decision.

STATES, NEVER COLLAPSED
-----------------------
  above_cap_measured  a cap measured for THIS leg, and tp_r exceeds it
  above_cap_sibling   derived from a same-symbol+timeframe sibling's measured
                      cap. cap_r is a function of entry, ATR and atr_stop_mult
                      — none of which is account-specific — so a `_prop` leg
                      shares its sibling's ceiling. Sound, but it is an
                      INFERENCE and is labelled as one rather than merged into
                      `measured`.
  within_cap          measured (or sibling-derived) and the target fits
  sentinel            tp_r >= 50: not a target at all, so nothing to check
  no_cap_basis        WE DID NOT LOOK. Never "fine" — the required ATR/entry
                      is reported so the reader can check it, and that is all.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

_REPO = Path(__file__).resolve().parents[2]

# The venue TP clamp -- ONE owner: src/runtime/tp_venue_cap.py. IMPORTED, not
# mirrored. This file used to carry its own `TP_VENUE_CAP_PCT = 0.099`, one of
# thirteen such literals with nothing binding them -- and this repo's own note
# on that was right: "if the live constant moves, this silently keeps measuring
# the OLD book, and the sweep will look correct while doing it". The owner
# imports only `typing`, and src/__init__.py + src/runtime/__init__.py are
# empty, so this adds no heavy dependency.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.runtime.tp_venue_cap import (  # noqa: E402
    TP_VENUE_CAP_PCT as TP_VENUE_CAP_PCT)
SENTINEL_R_FLOOR = 50.0

# Measured per-leg cap_r AT atr_stop_mult 2.5 (docs/research/
# bracket-target-reachability-2026-08-24.md). cap_r scales as 1/atr_stop_mult,
# so a leg declaring a different stop is rescaled rather than compared blind.
MEASURED_CAP_R: Dict[str, float] = {
    "trend_donchian": 5.98, "trend_donchian_1h": 5.38,
    "trend_donchian_eth": 4.08, "trend_donchian_sol": 3.22,
    "trend_donchian_avax_4h": 1.48, "trend_donchian_eth_4h": 2.04,
    "trend_donchian_xrp_4h": 2.11, "trend_donchian_ada_4h": 1.57,
    "trend_donchian_sol_4h": 1.44,
}
MEASURED_AT_STOP_MULT = 2.5


def sibling_of(leg: str) -> Optional[str]:
    """The measured leg a `_prop` variant inherits its ceiling from.

    Only ever strips a KNOWN account-variant suffix. It must not guess at
    general name similarity: `trend_donchian_sol` and `trend_donchian_sol_4h`
    are different TIMEFRAMES with genuinely different caps (3.22 vs 1.44), and
    a fuzzy match would hand one leg the other's ceiling.
    """
    for suffix in ("_prop",):
        if leg.endswith(suffix):
            base = leg[: -len(suffix)]
            if base in MEASURED_CAP_R:
                return base
    return None


def cap_for(leg: str, stop_mult: Optional[float]) -> Tuple[Optional[float], str]:
    """(cap_r, basis) for `leg`, rescaled to its own declared stop."""
    basis, measured = "measured", MEASURED_CAP_R.get(leg)
    if measured is None:
        sib = sibling_of(leg)
        if sib is not None:
            basis, measured = "sibling", MEASURED_CAP_R[sib]
    if measured is None:
        return None, "none"
    if stop_mult and stop_mult > 0 and stop_mult != MEASURED_AT_STOP_MULT:
        # cap_r = cap_pct / (stop_mult * ATR/entry) -> inversely proportional.
        measured = measured * (MEASURED_AT_STOP_MULT / stop_mult)
    return measured, basis


def required_atr_over_entry(tp_r: float, stop_mult: float) -> Optional[float]:
    """The ATR/entry a leg needs for `tp_r` to be reachable. A CONDITION."""
    if tp_r <= 0 or stop_mult <= 0:
        return None
    return TP_VENUE_CAP_PCT / (tp_r * stop_mult)


def grade(leg: str, tp_r: Optional[float],
          stop_mult: Optional[float]) -> Dict[str, object]:
    if tp_r is None:
        return {"leg": leg, "state": "no_target_declared"}
    if tp_r >= SENTINEL_R_FLOOR:
        return {"leg": leg, "state": "sentinel", "tp_r": tp_r}
    cap, basis = cap_for(leg, stop_mult)
    if cap is None:
        need = (required_atr_over_entry(tp_r, stop_mult)
                if stop_mult else None)
        return {"leg": leg, "state": "no_cap_basis", "tp_r": tp_r,
                "required_atr_over_entry": need}
    state = ("above_cap_measured" if tp_r > cap and basis == "measured"
             else "above_cap_sibling" if tp_r > cap
             else "within_cap")
    return {"leg": leg, "state": state, "tp_r": tp_r,
            "cap_r": round(cap, 4), "basis": basis}


def _load_legs():
    import yaml
    doc = yaml.safe_load((_REPO / "config" / "strategies.yaml").read_text())
    strategies = doc.get("strategies", doc)
    for name, cfg in strategies.items():
        if isinstance(cfg, dict):
            yield name, cfg


def selftest() -> int:
    checks, failed = [], []

    def ck(label, cond):
        checks.append(label)
        if not cond:
            failed.append(label)

    ck("a sentinel is not graded as a target",
       grade("x", 50.0, 2.5)["state"] == "sentinel")
    ck("an unmeasured leg is no_cap_basis, never within_cap",
       grade("unknown_leg", 3.0, 2.5)["state"] == "no_cap_basis")
    ck("no_cap_basis still reports the required volatility",
       grade("unknown_leg", 3.0, 2.5)["required_atr_over_entry"] is not None)
    ck("a target above a MEASURED cap is flagged",
       grade("trend_donchian_sol_4h", 6.0, 2.5)["state"] == "above_cap_measured")
    ck("a target under a measured cap passes",
       grade("trend_donchian", 3.0, 2.5)["state"] == "within_cap")
    ck("a _prop leg inherits its sibling's cap, LABELLED as sibling",
       grade("trend_donchian_sol_prop", 6.0, 2.5)["state"] == "above_cap_sibling")
    # The rescale must be real: at a WIDER stop the ceiling drops, so a target
    # that fits at 2.5 can stop fitting at 5.0.
    ck("a wider stop lowers the ceiling",
       grade("trend_donchian", 3.0, 5.0)["cap_r"] < grade(
           "trend_donchian", 3.0, 2.5)["cap_r"])
    ck("sibling matching never crosses timeframes",
       sibling_of("trend_donchian_sol_4h") is None)
    ck("required ATR/entry is inversely proportional to tp_r",
       required_atr_over_entry(6.0, 2.5) < required_atr_over_entry(3.0, 2.5))

    print(f"target-reachability report self-test: {len(checks)} checks")
    for f in failed:
        print(f"  FAIL: {f}")
    if failed:
        return 1
    print("  all pass — the report can distinguish every state it claims to")
    return 0


def main(argv) -> int:
    if "--self-test" in argv:
        return selftest()
    if selftest() != 0:                     # mechanism failure IS an error
        return 1

    rows = [grade(name, cfg.get("tp_r"), cfg.get("atr_stop_mult"))
            for name, cfg in _load_legs()]
    real = [r for r in rows if r["state"] not in
            ("sentinel", "no_target_declared")]
    warn = [r for r in real if str(r["state"]).startswith("above_cap")]

    print("\n=== target reachability (WARN-ONLY — findings never fail CI) ===")
    print(f"legs scanned: {len(rows)}  ·  declaring a REAL target: {len(real)}"
          f"  ·  sentinel: {sum(1 for r in rows if r['state'] == 'sentinel')}")

    for r in sorted(real, key=lambda r: str(r["state"])):
        if str(r["state"]).startswith("above_cap"):
            print(f"  ⚠️  {r['leg']}: tp_r {r['tp_r']} > cap_r "
                  f"{r['cap_r']} ({r['basis']}-derived) — this target is "
                  f"COSMETIC; it will be clamped and the leg behaves as if "
                  f"nothing were declared.")
        elif r["state"] == "no_cap_basis":
            need = r.get("required_atr_over_entry")
            need_s = f"{need:.4%}" if need else "unknown"
            print(f"  ·   {r['leg']}: tp_r {r['tp_r']} — NO measured cap for "
                  f"this leg (we did not look). Reachable only where "
                  f"ATR/entry <= {need_s}.")
        else:
            print(f"  ok  {r['leg']}: tp_r {r['tp_r']} <= cap_r {r['cap_r']} "
                  f"({r['basis']})")

    print(f"\n{len(warn)} cosmetic target(s) found. Reported, not enforced — "
          f"operator decision 2026-08-24 was warn, do not refuse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
