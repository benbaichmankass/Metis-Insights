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

  * counting `tp_r >= 50` **in the YAML** gives **23 of 44** enabled+live legs
  * the number of legs that BEHAVE as sentinels is **33 of 44**

(Measured at 814d019b. The earlier figures in this header were 24 of 45 and
34 of 45; both moved because unrelated PRs demoted a leg, NOT because the gap
changed — it is still exactly 10. Quote the run, not this paragraph.)

The gap is 10 `pullback` legs that declare no target key at all and inherit
`tp_r = 50.0` from their strategy class (`htf_pullback_trend_2h.py`), so the
YAML count understates the population by 43% — stating the basis, because it
is ambiguous and was not stated before: that is gap/declared (10/23). On
gap/effective it is 30% (10/33). Either way the direction is the same and the
declared count is the one that under-reports. The soak confirms it from the
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

# --- tp_intent: WHY a leg has the target it has -------------------------------
# The CONSUMER half of `config/strategies.yaml`'s `tp_intent` key (MI-156/MI-158,
# operator-approved 2026-09-07). This is not decoration: writing the inherited
# sentinel out explicitly on the 9 sub-population-B legs COLLAPSES this script's
# own `target_origin` signal -- `inherited_class_default` went 10 -> 0 at a
# stroke, so after that change `target_origin` alone can no longer tell a leg
# that was DELIBERATELY left unbracketed from one whose target question was
# NEVER ASKED. Both now read `declared_in_yaml` + `is_sentinel`. `tp_intent` is
# the field that keeps them apart, and this module is what branches on it.
#
# ⚠️ THE THREE STATES ARE NEVER SUMMED (docs/CLAUDE-RULES-CANONICAL.md §
# "Collapsed states"). `none` is a DECISION -- the trail is the profit-exit, or
# a target exists but live cannot express it. `unexamined` is an ABSENCE OF
# INQUIRY -- the leg is in neither sweep corpus. Folding `unexamined` into
# `none` would launder *we did not look* into *we decided*, which is the exact
# defect this vocabulary was accepted to prevent.
INTENT_R_MULTIPLE = "r_multiple"     # a real target is declared
INTENT_NONE = "none"                 # deliberate: no target, and we say why
INTENT_UNEXAMINED = "unexamined"     # WE DID NOT LOOK -- never a decision
INTENT_UNDECLARED = "undeclared"     # no tp_intent key: the pre-MI-158 state
INTENT_STATES = (INTENT_R_MULTIPLE, INTENT_NONE, INTENT_UNEXAMINED,
                 INTENT_UNDECLARED)


def resolve_intent(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """-> {mode, reason, evidence, revisit_when, calibrated}. Never raises.

    A malformed or non-mapping `tp_intent` resolves to INTENT_UNDECLARED rather
    than to a state -- a typo must never be readable as a decision.
    """
    raw = cfg.get("tp_intent")
    if not isinstance(raw, dict):
        return {"mode": INTENT_UNDECLARED, "reason": None, "basis": None,
                "evidence": None, "revisit_when": None, "calibrated": None}
    mode = raw.get("mode")
    if mode not in (INTENT_R_MULTIPLE, INTENT_NONE, INTENT_UNEXAMINED):
        mode = INTENT_UNDECLARED
    return {"mode": mode, "reason": raw.get("reason"),
            "basis": raw.get("basis"), "evidence": raw.get("evidence"),
            "revisit_when": raw.get("revisit_when"),
            "calibrated": raw.get("calibrated")}


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
        intent = resolve_intent(cfg)
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
            "tp_intent_mode": intent["mode"],
            "tp_intent_reason": intent["reason"],
            "tp_intent_basis": intent["basis"],
            "tp_intent_calibrated": intent["calibrated"],
            "tp_intent_revisit_when": intent["revisit_when"],
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
    # --- BRANCH ON tp_intent -------------------------------------------------
    # This is the section `target_origin` can no longer produce. Once the
    # inherited sentinels are written out explicitly, EVERY sentinel reads
    # `declared_in_yaml`; only `tp_intent.mode` separates a decision from an
    # unasked question. The three buckets are printed separately and NEVER
    # summed -- summing them is the collapse this exists to prevent.
    sent = [leg for leg in live if leg["is_sentinel"]]
    by_intent = {m: [leg for leg in sent if leg["tp_intent_mode"] == m]
                 for m in INTENT_STATES}
    p("=== WHY EACH ENABLED+LIVE SENTINEL HAS NO TARGET (tp_intent) : %d sentinels ==="
      % len(sent))
    p("    DELIBERATE / UNEXAMINED / UNDECLARED ARE THREE STATES, NEVER SUMMED.")
    p("")
    p("    DELIBERATE (mode: none) — a decision, recorded with its reason : %d"
      % len(by_intent[INTENT_NONE]))
    for reason in sorted({leg["tp_intent_reason"] for leg in by_intent[INTENT_NONE]},
                         key=lambda x: (x is None, x)):
        sub = [leg for leg in by_intent[INTENT_NONE] if leg["tp_intent_reason"] == reason]
        p("        reason=%-34s %d  %s"
          % (reason, len(sub), ", ".join(leg["name"] for leg in sub)))
    p("")
    p("    ⚠️ UNEXAMINED (mode: unexamined) — WE DID NOT LOOK, not a decision : %d"
      % len(by_intent[INTENT_UNEXAMINED]))
    for leg in by_intent[INTENT_UNEXAMINED]:
        p("        %-24s reason=%s revisit_when=%s"
          % (leg["name"], leg["tp_intent_reason"], leg["tp_intent_revisit_when"]))
    if not by_intent[INTENT_UNEXAMINED]:
        p("        (none — but a count of 0 here is only as good as the declarations)")
    p("")
    p("    UNDECLARED (no tp_intent key) — the remaining work : %d"
      % len(by_intent[INTENT_UNDECLARED]))
    for leg in by_intent[INTENT_UNDECLARED]:
        p("        %-24s family=%-9s tf=%s"
          % (leg["name"], leg["family"], leg["timeframe"]))
    p("")
    # A declared target that is REACHABLE but explicitly NOT CALIBRATED must never
    # be quoted as evidence of where momentum runs out (MI-156 § 8).
    uncal = [leg for leg in live
             if leg["tp_intent_mode"] == INTENT_R_MULTIPLE
             and leg["tp_intent_calibrated"] is False]
    p("=== DECLARED TARGET, EXPLICITLY *NOT* CALIBRATED : %d ===" % len(uncal))
    p("    Reachable != calibrated. Do NOT quote these as evidenced targets.")
    for leg in uncal:
        p("        %-24s target_r=%-6s basis=%s"
          % (leg["name"], leg["target_r_effective"], leg["tp_intent_basis"]))
    if not uncal:
        p("        (none)")
    p("")
    unreach = [leg for leg in live if leg["reachable"] is False]
    p("=== REAL TARGET THE VENUE WOULD CLAMP at the reference ATR : %d ===" % len(unreach))
    for leg in unreach:
        p("    %-24s target_r=%-6s cap_r=%.3f  (stop %.2f ATR)"
          % (leg["name"], leg["target_r_effective"], leg["cap_r_at_ref_atr"], leg["atr_stop_mult"]))
    if not unreach:
        p("    (none at this reference ATR — reachability is ATR-dependent, so this")
        p("     is NOT proof a target rests; read live cap_r from the soak.)")
    else:
        # ⚠️ The populated case is the one that misleads, and it had no caveat.
        # cap_r@ref assumes ATR/entry = 0.02, which is crypto-shaped and
        # MATERIALLY UNDERSTATES cap_r on low-volatility equity/ETF legs. Worked
        # example (MI-156 § 3): gld_pullback_1h lists here at cap_r@ref 1.98,
        # while its MEASURED cap_r over n=4 live telemetry rows is min 7.94 /
        # median 8.24 — so its 4.0R target BINDS on 4 of 4 observed trades and
        # this section's "would clamp" is an artefact of the reference, not a
        # finding. Read live cap_r from the soak before acting on any row here.
        p("    ⚠️ AT THE REFERENCE ATR ONLY — NOT a measurement. cap_r@ref assumes")
        p("       ATR/entry=0.02 and understates cap_r on low-vol equity/ETF legs")
        p("       (gld_pullback_1h: ref 1.98 vs MEASURED median 8.24, n=4). Read")
        p("       live cap_r from the soak before treating any row as clamped.")


def selftest() -> int:
    fails = []
    ran = []
    def chk(label, got, want):
        ran.append(label)
        if got != want:
            fails.append("%s: got %r want %r" % (label, got, want))
    # the constants are IMPORTED from the one owner now, not mirrored, so the
    # check is identity rather than a text search for a literal that no longer
    # exists in the source (it moved to src/runtime/tp_venue_cap.py).
    from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT as _owner_cap
    chk("TP_VENUE_CAP_PCT IS the owner", TP_VENUE_CAP_PCT is _owner_cap, True)
    src = (REPO / "src/runtime/target_expectation.py").read_text()
    chk("SENTINEL_R_FLOOR mirrors module", "SENTINEL_R_FLOOR = 50.0" in src, True)
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
    # --- tp_intent: the three states must stay separable ---------------------
    # These pin the NON-COLLAPSE property itself, not a population count: a
    # future edit that maps `unexamined` onto `none` fails here.
    chk("undeclared when key absent", resolve_intent({})["mode"], INTENT_UNDECLARED)
    chk("none parsed", resolve_intent({"tp_intent": {"mode": "none"}})["mode"],
        INTENT_NONE)
    chk("unexamined parsed",
        resolve_intent({"tp_intent": {"mode": "unexamined"}})["mode"],
        INTENT_UNEXAMINED)
    chk("unexamined IS NOT none",
        resolve_intent({"tp_intent": {"mode": "unexamined"}})["mode"]
        == resolve_intent({"tp_intent": {"mode": "none"}})["mode"], False)
    chk("undeclared IS NOT none",
        resolve_intent({})["mode"]
        == resolve_intent({"tp_intent": {"mode": "none"}})["mode"], False)
    # A typo is never readable as a decision.
    chk("unknown mode -> undeclared",
        resolve_intent({"tp_intent": {"mode": "nope"}})["mode"], INTENT_UNDECLARED)
    chk("non-mapping -> undeclared",
        resolve_intent({"tp_intent": "none"})["mode"], INTENT_UNDECLARED)
    chk("calibrated:false survives",
        resolve_intent({"tp_intent": {"mode": "r_multiple",
                                      "calibrated": False}})["calibrated"], False)
    # ...and the live config actually exercises all three states.
    modes = {leg["tp_intent_mode"] for leg in legs}
    chk("config exercises none", INTENT_NONE in modes, True)
    chk("config exercises unexamined", INTENT_UNEXAMINED in modes, True)
    chk("config exercises r_multiple", INTENT_R_MULTIPLE in modes, True)

    for f in fails:
        print("FAIL " + f)
    print("selftest: %d/%d passed" % (len(ran) - len(fails), len(ran)))
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=str(REPO / "config/strategies.yaml"))
    ap.add_argument("--atr-over-entry", type=float, default=0.02,
                    help="reference ATR/entry for the cap_r column (default 0.02). "
                         "cap_r is ATR-dependent; this is a REFERENCE, not a measurement.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--assert-intent-declared", action="store_true",
                    help="exit 1 if any enabled+live SENTINEL leg declares no "
                         "tp_intent. Asserts a property, never a count.")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    data = census(Path(a.config), a.atr_over_entry)
    if a.assert_intent_declared:
        # Manual gate, deliberately NOT wired to CI: this script's header says a
        # CI job asserting a particular count would fail every time a leg is
        # legitimately retuned. This asserts a PROPERTY, not a count -- every
        # enabled+live sentinel states WHY -- and is run by a session that means
        # to drive the residue to zero.
        live_ = [leg for leg in data["legs"]
                 if leg["enabled"] and leg["execution"] == "live"]
        gap = [leg for leg in live_ if leg["is_sentinel"]
               and leg["tp_intent_mode"] == INTENT_UNDECLARED]
        if gap:
            print("bracket-expectation-census: FAIL — %d enabled+live sentinel(s) "
                  "declare no tp_intent:" % len(gap), file=sys.stderr)
            for leg in gap:
                print("    %s" % leg["name"], file=sys.stderr)
            return 1
        print("bracket-expectation-census: OK — every enabled+live sentinel "
              "declares a tp_intent.")
        return 0
    if a.json:
        json.dump(data, sys.stdout, indent=2)
        return 0
    report(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
