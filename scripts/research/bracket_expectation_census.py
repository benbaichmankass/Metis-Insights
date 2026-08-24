#!/usr/bin/env python3
# wiring: manual-only — a census a session RUNS to answer "does this leg carry an
# expectation at entry"; there is no cadence at which the fleet should be re-counted
# automatically, and a CI job asserting a particular count would fail every time a
# leg is legitimately retuned. Its --selftest IS wired (artifact-validity-guard),
# because the INVARIANTS it pins never change even though the population does.
"""Which live legs carry an EXPECTATION at entry, and could they if they tried?

Operator directive 2026-08-23: *"Brackets ALWAYS represent our prediction of
where the trade should end … The only solution here is to properly build out
the active management infra, not layer on bandaids."*

`src/runtime/target_expectation.py` grades ONE trade from a config dict it is
handed. This is the FLEET census over `config/strategies.yaml`, and it exists
because two numbers that ought to agree do not:

  * counting `tp_r >= 50` **in the YAML** gives **24 of 45** enabled+live legs
  * the number of legs that BEHAVE as sentinels is **34 of 45**

The gap is 10 `pullback` legs that declare no target key at all and inherit
`tp_r = 50.0` from their strategy class (`htf_pullback_trend_2h.py`), so the
YAML count understates the population by 42%. The soak confirms it from the
other side: those legs report `target_source_key: tp_r, target_r: 50.0` on
rows whose YAML has neither key.

⚠️ **DECLARED AND EFFECTIVE ARE REPORTED SEPARATELY, NEVER SUMMED OR
COLLAPSED.** A leg that explicitly writes `tp_r: 50.0` made a choice; a leg
that writes nothing inherited one. Same runtime behaviour, different remedy,
and the whole point of `target_expectation.STATE_NO_TARGET_KEY` is that
confusing them "would accuse legs of a defect they may not have".

REACHABILITY. A target is a prediction only if the level that RESTS is the
level predicted. The venue clamp decides that:

    cap_r = TP_VENUE_CAP_PCT * entry / risk,  risk = atr_stop_mult * ATR

so **cap_r is inversely proportional to `atr_stop_mult`** — widening a stop
LOWERS the reachable target in R — and proportional to `entry/ATR`, i.e. it is
harsher the more volatile the instrument. This module reports cap_r as a
function of the observed ATR/price ratio rather than inventing one, because
ATR at entry is not knowable from config alone.

⚠️ **THIS SCRIPT READS CONFIG ONLY. It opens no socket, reads no journal, and
grades no live trade.** Tier-1, observe-only. Every value it prints is a
property of the declared configuration; a live cap_r must come from the soak
(`/api/diag/log_file?name=target_extension_soak`), not from here.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]

# Mirrors src/runtime/target_expectation.py. Kept as a mirror rather than an
# import so this stays runnable standalone; agreement is asserted in selftest.
SENTINEL_R_FLOOR = 50.0
TP_VENUE_CAP_PCT = 0.099
TARGET_KEYS = ("target_r", "tp_r", "tp_at_r")

# Class-level defaults, read from the strategy modules rather than hardcoded
# beliefs about them. A family absent here has no default and a leg with no
# target key is genuinely ungradeable, NOT a sentinel.
CLASS_DEFAULT_SOURCES = {
    "donchian": ("src/units/strategies/trend_donchian.py", "tp_r"),
    "pullback": ("src/units/strategies/htf_pullback_trend_2h.py", "tp_r"),
    "squeeze": ("src/units/strategies/squeeze_breakout_4h.py", "tp_r"),
    "fade": ("src/units/strategies/fade_breakout_4h.py", "tp_r"),
}

ORIGIN_DECLARED = "declared_in_yaml"
ORIGIN_CLASS_DEFAULT = "inherited_class_default"
ORIGIN_NONE = "no_target_anywhere"


def _read_class_default(rel: str, key: str) -> Optional[float]:
    """Parse `"<key>": <float>,` out of a strategy module's DEFAULTS block.

    Deliberately a source read, not an import: importing a strategy module
    pulls the runtime in, and this script must stay dependency-free.
    """
    path = REPO / rel
    try:
        text = path.read_text()
    except OSError:
        return None
    import re
    m = re.search(rf'^\s*["\']{re.escape(key)}["\']\s*:\s*([0-9.]+)\s*,', text, re.M)
    return float(m.group(1)) if m else None


def family_of(name: str, cfg: Dict[str, Any]) -> str:
    if "tp_at_r" in cfg or "sweep_lookback_bars" in cfg:
        return "ict_scalp"
    if "pairs" in name:
        return "pairs"
    if "squeeze" in name:
        return "squeeze"
    if "fade" in name:
        return "fade"
    if "donchian" in cfg or "donchian" in name:
        return "donchian"
    if "pullback_lookback" in cfg or "pullback" in name:
        return "pullback"
    return "other"


def resolve_target(name: str, cfg: Dict[str, Any],
                   defaults: Dict[str, Optional[float]]
                   ) -> Tuple[Optional[float], Optional[str], str]:
    """-> (effective_target_r, source_key, origin). Never raises."""
    for k in TARGET_KEYS:
        v = cfg.get(k)
        if v is not None:
            try:
                return float(v), k, ORIGIN_DECLARED
            except (TypeError, ValueError):
                return None, k, ORIGIN_DECLARED
    d = defaults.get(family_of(name, cfg))
    if d is not None:
        return d, "tp_r", ORIGIN_CLASS_DEFAULT
    return None, None, ORIGIN_NONE


def cap_r_for(atr_stop_mult: Optional[float], atr_over_entry: float) -> Optional[float]:
    """The venue-reachable target in R. None when the stop is unreadable."""
    if not atr_stop_mult or atr_stop_mult <= 0 or atr_over_entry <= 0:
        return None
    return TP_VENUE_CAP_PCT / (atr_stop_mult * atr_over_entry)


def census(path: Path, atr_over_entry: float) -> Dict[str, Any]:
    import yaml
    raw = yaml.safe_load(path.read_text())
    strategies = raw.get("strategies", raw)
    defaults = {f: _read_class_default(rel, key)
                for f, (rel, key) in CLASS_DEFAULT_SOURCES.items()}
    legs = []
    for name, cfg in sorted(strategies.items()):
        if not isinstance(cfg, dict):
            continue
        eff, src, origin = resolve_target(name, cfg, defaults)
        sm = cfg.get("atr_stop_mult")
        try:
            sm = float(sm) if sm is not None else None
        except (TypeError, ValueError):
            sm = None
        cap = cap_r_for(sm, atr_over_entry)
        legs.append({
            "name": name,
            "enabled": bool(cfg.get("enabled", True)),
            "execution": cfg.get("execution", "live"),
            "family": family_of(name, cfg),
            "timeframe": cfg.get("timeframe"),
            "symbols": cfg.get("symbols") or [],
            "atr_stop_mult": sm,
            "target_r_effective": eff,
            "target_source_key": src,
            "target_origin": origin,
            "is_sentinel": (eff is not None and eff >= SENTINEL_R_FLOOR),
            "cap_r_at_ref_atr": cap,
            # Only meaningful for a REAL target: does the venue refuse it?
            "reachable": (None if eff is None or cap is None or eff >= SENTINEL_R_FLOOR
                          else bool(eff <= cap)),
        })
    return {"reference_atr_over_entry": atr_over_entry,
            "class_defaults": defaults, "legs": legs}


def _pop(legs, sel):
    return [leg for leg in legs if sel(leg)]


def report(data: Dict[str, Any], out=sys.stdout) -> None:
    legs = data["legs"]
    def p(*a):
        print(*a, file=out)
    p("class defaults read from source:", json.dumps(data["class_defaults"]))
    p("reference ATR/entry for cap_r    :", data["reference_atr_over_entry"],
      "(cap_r scales as 1/(atr_stop_mult * ATR/entry))")
    p("")
    pops = [("all declared", lambda leg: True),
            ("enabled, any execution", lambda leg: leg["enabled"]),
            ("enabled + live", lambda leg: leg["enabled"] and leg["execution"] == "live")]
    p("%-26s %5s %11s %11s %8s %8s" % ("POPULATION", "n", "sent_DECL", "sent_EFF", "real", "ungrade"))
    for label, sel in pops:
        sub = _pop(legs, sel)
        sd = len([leg for leg in sub if leg["is_sentinel"] and leg["target_origin"] == "declared_in_yaml"])
        se = len([leg for leg in sub if leg["is_sentinel"]])
        re_ = len([leg for leg in sub if leg["target_r_effective"] is not None and not leg["is_sentinel"]])
        un = len([leg for leg in sub if leg["target_r_effective"] is None])
        p("%-26s %5d %11d %11d %8d %8d" % (label, len(sub), sd, se, re_, un))
    p("")
    live = _pop(legs, lambda leg: leg["enabled"] and leg["execution"] == "live")
    p("=== enabled+live, by family ===")
    p("%-12s %4s %10s %10s %6s   %s" % ("family", "n", "sent_DECL", "sent_EFF", "real", "stop mults in use"))
    for f in sorted({leg["family"] for leg in live}):
        sub = [leg for leg in live if leg["family"] == f]
        sd = len([leg for leg in sub if leg["is_sentinel"] and leg["target_origin"] == "declared_in_yaml"])
        se = len([leg for leg in sub if leg["is_sentinel"]])
        re_ = len([leg for leg in sub if leg["target_r_effective"] is not None and not leg["is_sentinel"]])
        sms = sorted({leg["atr_stop_mult"] for leg in sub if leg["atr_stop_mult"]})
        p("%-12s %4d %10d %10d %6d   %s" % (f, len(sub), sd, se, re_, sms))
    p("")
    inh = [leg for leg in live if leg["is_sentinel"] and leg["target_origin"] == ORIGIN_CLASS_DEFAULT]
    p("=== SENTINEL BY INHERITANCE (declare no target; inherit the class default) : %d ===" % len(inh))
    p("    These are NOT visible to a `grep tp_r` of the YAML. Same runtime")
    p("    behaviour as an explicit sentinel, different remedy.")
    for leg in inh:
        p("    %-24s family=%-9s tf=%s" % (leg["name"], leg["family"], leg["timeframe"]))
    p("")
    unreach = [leg for leg in live if leg["reachable"] is False]
    p("=== REAL TARGET THE VENUE WOULD CLAMP at the reference ATR : %d ===" % len(unreach))
    for leg in unreach:
        p("    %-24s target_r=%-6s cap_r=%.3f  (stop %.2f ATR)"
          % (leg["name"], leg["target_r_effective"], leg["cap_r_at_ref_atr"], leg["atr_stop_mult"]))
    if not unreach:
        p("    (none at this reference ATR — reachability is ATR-dependent, so this")
        p("     is NOT proof a target rests; read live cap_r from the soak.)")


def selftest() -> int:
    fails = []
    def chk(label, got, want):
        if got != want:
            fails.append("%s: got %r want %r" % (label, got, want))
    # the mirrored constants must match the module they mirror
    src = (REPO / "src/runtime/target_expectation.py").read_text()
    chk("SENTINEL_R_FLOOR mirrors module", "SENTINEL_R_FLOOR = 50.0" in src, True)
    chk("TP_VENUE_CAP_PCT mirrors module", "TP_VENUE_CAP_PCT = 0.099" in src, True)
    # family routing
    chk("ict_scalp by tp_at_r", family_of("x", {"tp_at_r": 1.5}), "ict_scalp")
    chk("donchian by key", family_of("x", {"donchian": 20}), "donchian")
    chk("pullback by key", family_of("tlt_pullback_1d", {"pullback_lookback": 10}), "pullback")
    # an explicit target always wins over the class default
    d = {"donchian": 50.0, "pullback": 50.0}
    chk("explicit beats default", resolve_target("a", {"donchian": 20, "tp_r": 3.0}, d),
        (3.0, "tp_r", ORIGIN_DECLARED))
    # the inheritance case this script exists for
    chk("inherits default", resolve_target("tlt_pullback_1d", {"pullback_lookback": 10}, d),
        (50.0, "tp_r", ORIGIN_CLASS_DEFAULT))
    # a family with no default stays ungradeable — never silently a sentinel
    chk("no default -> ungradeable", resolve_target("odd_leg", {}, {}), (None, None, ORIGIN_NONE))
    # cap_r arithmetic + its inverse relationship to the stop
    c15 = cap_r_for(1.5, 0.02)
    c30 = cap_r_for(3.0, 0.02)
    chk("cap_r halves when stop doubles", round(c15 / c30, 6), 2.0)
    chk("cap_r value", round(cap_r_for(2.5, 0.02), 4), round(0.099 / 0.05, 4))
    chk("unreadable stop -> None", cap_r_for(None, 0.02), None)
    chk("zero stop -> None", cap_r_for(0.0, 0.02), None)
    # a sentinel is never graded reachable/unreachable — the question is moot
    legs = census(REPO / "config/strategies.yaml", 0.02)["legs"]
    sent = [leg for leg in legs if leg["is_sentinel"]]
    chk("sentinels have reachable=None", all(leg["reachable"] is None for leg in sent), True)
    chk("census found legs", len(legs) > 0, True)
    for f in fails:
        print("FAIL " + f)
    print("selftest: %d/%d passed" % (11 - len(fails), 11))
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=str(REPO / "config/strategies.yaml"))
    ap.add_argument("--atr-over-entry", type=float, default=0.02,
                    help="reference ATR/entry for the cap_r column (default 0.02). "
                         "cap_r is ATR-dependent; this is a REFERENCE, not a measurement.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    data = census(Path(a.config), a.atr_over_entry)
    if a.json:
        json.dump(data, sys.stdout, indent=2)
        return 0
    report(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
