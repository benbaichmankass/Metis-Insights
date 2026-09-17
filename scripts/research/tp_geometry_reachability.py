#!/usr/bin/env python3
# wiring: manual-only — a one-shot audit answering "can a labelled axis VARY inside
# the unit it is compared across", i.e. whether a prescribed remedy for a confound is
# even runnable. It grades the PRODUCERS (tp_geometry_for, base_args) rather than a
# live feed, so its answer changes only when they do; a cadence would re-derive the
# same verdict on every run and train a reader past it. Re-run it by hand when a
# family, a clamping set, or the label owner changes. Registered in
# docs/research/RESEARCH-CAPABILITY-INDEX.md.
"""Can `tp_geometry` ever VARY inside one family — and is the prescribed remedy runnable?

MI-278 U48. Works
`BL-20260912-THE-M20-WALKFORWARD-ARM-SET-CANNOT-GRADE-TARGET-GEOMETRY-BECAUSE-TP-GEOMETRY-IS-PERFECTLY-CONFOUNDED-WITH-FAMILY-AND-BLOCK-UNIT`.

THE ROW IS RIGHT AND STOPS ONE STEP SHORT, AND THAT STEP IS THE ONE THAT DECIDES
WHAT ANYONE SHOULD RUN NEXT. It grades `tp_geometry` **perfectly confounded**
with family over the 246-arm corpus, which is a statement about a SAMPLE — and a
confound in a sample is the kind of thing a better-designed sample breaks. So its
`next_step` prescribes designing one: *"run live_parity_capped AND
live_parity_uncapped on the SAME family with the SAME block_unit (pullback is the
obvious choice)"*.

⚠️ **THAT ARM CANNOT BE BUILT, ON ANY FAMILY, AT ANY CAP.** `tp_geometry` is not
a knob that happened to co-vary with family; it is COMPUTED FROM family
membership in `CLAMPING_FAMILIES`, by the same predicate that decides whether the
cap reaches the harness at all. Within one family the label is therefore
invariant in the pair the contrast needs. A session that commissioned the
prescribed sweep would spend a trainer run and receive the dataset it already has.

**This module establishes that by MEASUREMENT against the real producers, never
by re-deriving them.** It imports `tp_geometry_for` (the declared single owner of
the label) and determines flag delivery by CALLING `base_args` and reading its
argv — so if either changes, this instrument changes with it rather than
preserving a stale copy of a rule.

⚠️ **UNREACHABILITY HERE IS PROVEN, NOT SAMPLED, AND THE PROOF IS ITS OWN
ASSERTION.** A grid walk could only ever say *"no cap I tried changed it"*. The
stronger claim holds because the cap enters `tp_geometry_for` **only** through
`tp_cap_pct > 0.0` — a sign test — so `{0.0, any one positive}` is EXHAUSTIVE
over the cap's influence. That is not assumed: `cap_enters_only_as_a_sign_test()`
asserts it over a spread of positive magnitudes, and a future change that made
the label depend on the cap's SIZE would fail it loudly and downgrade every
`unreachable_by_construction` verdict to `unknown`. Read that field before
quoting any verdict here.

⚠️ **WHAT THIS DOES NOT SAY.** It does not say target geometry is irrelevant, and
it does not say the 75%-vs-30% gap is noise. It says the gap is one observation
with two names, that no redesign of THIS axis can separate them, and that the
only separable axis left is the cap LEVEL inside a clamping family. Whether the
target should move is Tier-3 and nothing here proposes it.

Tier-1 research tooling. Reads a committed JSONL and two repo modules; writes
nothing the trader executes and touches no live path.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_ARMS = "docs/research/m20-fold-dispersion-arms-consolidated.jsonl"

# The two geometry levels the row's contrast is written in terms of. `NO_TAKE_PROFIT`
# is deliberately NOT one of them: it is a book with no target at all, not the
# uncapped-parity arm, and treating it as the second level would let a pullback
# run at cap 0 masquerade as the missing contrast.
CONTRAST_LEVELS = ("live_parity_capped", "live_parity_uncapped")

# States, never collapsed. `unknown` is *we could not look*, and is neither a
# reachable nor an unreachable finding.
PAIR_BOTH = "both_levels"
PAIR_CAPPED_ONLY = "capped_only"
PAIR_UNCAPPED_ONLY = "uncapped_only"
PAIR_NEITHER = "neither_level"
PAIR_UNKNOWN = "unknown"

DOSE_REACHABLE = "dose_reachable"
DOSE_INERT = "dose_inert"
DOSE_UNKNOWN = "unknown"

CLAUSE_SATISFIABLE = "satisfiable"
CLAUSE_UNSATISFIABLE = "unsatisfiable_by_construction"
CLAUSE_UNKNOWN = "unknown"


class ProducersUnavailable(RuntimeError):
    """The real producers could not be imported, so nothing may be graded."""


def load_producers() -> Dict[str, Any]:
    """Import the REAL label owner and argv builder. Never a local copy.

    Refuses rather than falling back to a re-derivation: a re-derived predicate
    that agreed today would silently stop agreeing the moment either producer
    moved, and this module's entire claim is about what those producers do.
    """
    try:
        import m20_fleet_exit_sweep as fs  # type: ignore
        from src.runtime.tp_venue_cap import (  # type: ignore
            CLAMPING_FAMILIES, TP_VENUE_CAP_PCT)
    except Exception as exc:  # allow-silent: nothing is swallowed — this RE-RAISES as
        # ProducersUnavailable with the cause attached, and every caller refuses to grade
        # rather than falling back to a re-derived predicate. Broad because an import can
        # fail as ImportError, OSError, SyntaxError or anything the imported module raises
        # at module scope, and ALL of them mean the same thing here: we cannot read the
        # producers, so we must not pretend to.  # noqa: BLE001
        raise ProducersUnavailable(str(exc)) from exc
    return {
        "tp_geometry_for": fs.tp_geometry_for,
        "base_args": fs.base_args,
        "clamping_families": frozenset(CLAMPING_FAMILIES),
        "venue_cap_pct": float(TP_VENUE_CAP_PCT),
    }


def flag_delivered(prod: Dict[str, Any], family: str, cap: float) -> Optional[bool]:
    """Did `--tp-cap-pct` actually REACH the harness for this (family, cap)?

    MEASURED by invoking the real ``base_args`` and reading the argv it returns,
    which is the only way to answer this without copying its predicate. Returns
    ``None`` — *we could not look* — when the call raises, never ``False``:
    "the flag was withheld" and "we could not build the argv" are different
    facts and only one of them is about the harness.
    """
    cfg = {"timeframe": "5m", "symbols": ["BTCUSDT"]}
    try:
        argv = prod["base_args"]("probe-leg", cfg, family, "probe.csv", None, cap)
    except Exception:  # allow-silent: catching everything IS the assertion — any failure
        # to build the argv means we could not look, which is returned as None and graded
        # `unknown`, never as False. Narrowing this would let an unanticipated exception
        # type crash a census whose whole purpose is to keep 'we did not look' distinct
        # from 'the flag was withheld'.  # noqa: BLE001
        return None
    return "--tp-cap-pct" in list(argv)


def cap_enters_only_as_a_sign_test(prod: Dict[str, Any],
                                   families: Sequence[str],
                                   *, magnitudes: Optional[Sequence[float]] = None,
                                   ) -> Dict[str, Any]:
    """Is the label a function of ``cap > 0`` ALONE, rather than of the cap's size?

    This is the assumption that upgrades every verdict below from *"no cap in my
    grid changed it"* to *"no cap can change it"*, so it is asserted rather than
    relied on. If a future ``tp_geometry_for`` graded, say, a cap above some
    threshold differently, ``holds`` goes False and the caller must downgrade
    every ``unreachable_by_construction`` to ``unknown``.

    ``holds: None`` is *we could not look* (no family produced a reading), never
    True.
    """
    mags = list(magnitudes) if magnitudes is not None else [
        1e-9, 1e-4, 0.001, 0.01, 0.05, 0.099, 0.25, 0.5, 1.0, 5.0, 1e3, 1e9]
    geom = prod["tp_geometry_for"]
    per_family: Dict[str, Any] = {}
    counterexamples: List[Dict[str, Any]] = []
    graded = 0
    for fam in families:
        labels: Set[str] = set()
        for m in mags:
            try:
                labels.add(str(geom([fam], m)))
            except Exception:  # allow-silent: any failure to label this family makes the
                # SIGN TEST ungradeable for it, which is recorded as None and EXCLUDED from
                # the denominator — it can only ever weaken `holds`, never manufacture it.
                # noqa: BLE001
                labels = set()
                break
        if not labels:
            per_family[fam] = None
            continue
        graded += 1
        per_family[fam] = sorted(labels)
        if len(labels) > 1:
            counterexamples.append({"family": fam, "labels": sorted(labels)})
    holds: Optional[bool]
    if graded == 0:
        holds = None
    else:
        holds = not counterexamples
    return {
        "holds": holds,
        "magnitudes_tried": mags,
        "families_graded": graded,
        "families_offered": len(list(families)),
        "per_family_labels": per_family,
        "counterexamples": counterexamples,
    }


def family_reachability(prod: Dict[str, Any], family: str,
                        *, positive_cap: Optional[float] = None) -> Dict[str, Any]:
    """Which geometry labels can this ONE family carry, and is the dose live?

    Two caps suffice and the reason is the sign-test property asserted above:
    ``0.0`` and one positive value exhaust the cap's influence on the label.
    """
    pos = float(positive_cap if positive_cap is not None else prod["venue_cap_pct"])
    geom = prod["tp_geometry_for"]
    readings: Dict[str, Any] = {}
    labels: Set[str] = set()
    ok = True
    for tag, cap in (("cap_zero", 0.0), ("cap_positive", pos)):
        try:
            lab = str(geom([family], cap))
        except Exception:  # allow-silent: a family we cannot label must grade
            # `pair_state: unknown`, which BLOCKS the absence claim in grade_clause_a
            # rather than contributing to it. Swallowing here makes the verdict more
            # conservative, never less.  # noqa: BLE001
            ok = False
            readings[tag] = {"cap": cap, "label": None, "flag_delivered": None}
            continue
        labels.add(lab)
        readings[tag] = {"cap": cap, "label": lab,
                         "flag_delivered": flag_delivered(prod, family, cap)}

    if not ok:
        pair = PAIR_UNKNOWN
    else:
        has_capped = CONTRAST_LEVELS[0] in labels
        has_uncapped = CONTRAST_LEVELS[1] in labels
        pair = (PAIR_BOTH if has_capped and has_uncapped
                else PAIR_CAPPED_ONLY if has_capped
                else PAIR_UNCAPPED_ONLY if has_uncapped
                else PAIR_NEITHER)

    delivered_positive = readings.get("cap_positive", {}).get("flag_delivered")
    if delivered_positive is None:
        dose = DOSE_UNKNOWN
    elif delivered_positive:
        dose = DOSE_REACHABLE
    else:
        dose = DOSE_INERT

    return {
        "family": family,
        "labels_attainable": sorted(labels),
        "label_varies": (len(labels) > 1) if ok else None,
        "pair_state": pair,
        "dose_state": dose,
        "readings": readings,
        "in_clamping_families": family in prod["clamping_families"],
    }


def grade_clause_a(per_family: Sequence[Dict[str, Any]],
                   sign_test: Dict[str, Any]) -> Dict[str, Any]:
    """The row's clause (A): does SOME family carry BOTH contrast levels?

    ``unsatisfiable_by_construction`` is claimed only when the sign test HOLDS —
    otherwise the grid was a sample and the honest answer is ``unknown``.
    """
    if not per_family:
        return {"state": CLAUSE_UNKNOWN,
                "why": "no family was graded, so nothing was looked at",
                "families_with_both": []}
    both = [f["family"] for f in per_family if f["pair_state"] == PAIR_BOTH]
    if both:
        return {"state": CLAUSE_SATISFIABLE,
                "why": "at least one family attains both contrast levels",
                "families_with_both": both}
    if any(f["pair_state"] == PAIR_UNKNOWN for f in per_family):
        return {"state": CLAUSE_UNKNOWN,
                "why": "a family could not be graded, so absence is not established",
                "families_with_both": []}
    if sign_test.get("holds") is not True:
        return {"state": CLAUSE_UNKNOWN,
                "why": ("the cap's influence was not established as a sign test, so "
                        "the two caps probed are a SAMPLE and cannot prove absence"),
                "families_with_both": []}
    return {"state": CLAUSE_UNSATISFIABLE,
            "why": ("no family attains both levels at ANY cap: the label is computed "
                    "from family membership in CLAMPING_FAMILIES, and the cap enters "
                    "only as a sign test"),
            "families_with_both": []}


def load_arms(path: str) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """Read the consolidated arm set. ``(None, reason)`` when we could not look."""
    if not os.path.exists(path):
        return None, f"absent: {path}"
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except (OSError, ValueError) as exc:
        return None, f"unreadable: {exc}"
    return rows, "read"


def arm_census(prod: Dict[str, Any], rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Reproduce the row's confound census, and grade DECLARED vs DELIVERED.

    The second half is the row's own explicitly-unestablished caveat: it noted
    that the uncapped arms carry ``tp_cap_pct: 0.099`` too and that this *"may
    mean the field records declared config rather than what was applied"*, which
    *"was not established"*. Establishing it is cheap and it is done here, by
    asking ``base_args`` whether that cap would have been delivered.
    """
    combos = collections.Counter()
    by_geom_family: Dict[str, Set[str]] = collections.defaultdict(set)
    by_family_geom: Dict[str, Set[str]] = collections.defaultdict(set)
    caps_by_geom: Dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    verdicts: Dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    delivered = collections.Counter()
    ungradeable = collections.Counter()

    for r in rows:
        g = r.get("tp_geometry")
        fam = r.get("family")
        bu = r.get("block_unit")
        cap = r.get("tp_cap_pct")
        if g is None or fam is None:
            ungradeable["missing_geometry_or_family"] += 1
            continue
        combos[(str(g), str(fam), str(bu))] += 1
        by_geom_family[str(g)].add(str(fam))
        by_family_geom[str(fam)].add(str(g))
        verdicts[str(g)][str(r.get("verdict"))] += 1
        if isinstance(cap, (int, float)):
            caps_by_geom[str(g)][float(cap)] += 1
            d = flag_delivered(prod, str(fam), float(cap))
            if d is None:
                ungradeable["flag_delivery_unreadable"] += 1
            else:
                delivered["delivered" if d else "recorded_but_never_delivered"] += 1
        else:
            ungradeable["non_numeric_tp_cap_pct"] += 1

    # A family carrying exactly one geometry across the whole corpus is the
    # confound, stated per family so it cannot be read off a single headline.
    single_geom = {f: sorted(gs) for f, gs in by_family_geom.items() if len(gs) == 1}

    return {
        "arms": len(rows),
        "legs": len({r.get("leg") for r in rows}),
        "families": sorted({str(r.get("family")) for r in rows if r.get("family")}),
        "combos": {" | ".join(k): v for k, v in sorted(combos.items())},
        "families_per_geometry": {k: sorted(v) for k, v in sorted(by_geom_family.items())},
        "families_with_one_geometry_only": single_geom,
        "caps_per_geometry": {k: dict(v) for k, v in sorted(caps_by_geom.items())},
        "verdicts_per_geometry": {k: dict(v) for k, v in sorted(verdicts.items())},
        "declared_vs_delivered": dict(delivered),
        "ungradeable": dict(ungradeable),
    }


def candidate_rate(rows: Sequence[Dict[str, Any]], key: str) -> Dict[str, Any]:
    """`candidate` share per value of `key`, with the denominator stated."""
    out: Dict[str, Any] = {}
    buckets: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        buckets[str(r.get(key))].append(r)
    for k, sub in sorted(buckets.items()):
        n = len(sub)
        c = sum(1 for r in sub if str(r.get("verdict")) == "candidate")
        out[k] = {"candidate": c, "n": n, "rate": (c / n) if n else None}
    return out


def within_geometry_spread(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """How much do FAMILIES differ INSIDE one geometry level?

    This bounds how much of the between-level gap the LEVEL can be carrying. If
    families inside a single label already differ by nearly as much as the labels
    differ from each other, then "the label explains the gap" is not merely
    unproven — it is unsupported by the same data that produced it.

    ``spread`` is ``None``, never ``0.0``, for a level carrying fewer than two
    families: one family has no spread, and reporting zero would read as
    "the families agreed".
    """
    by_level: Dict[str, Dict[str, List[Dict[str, Any]]]] = collections.defaultdict(
        lambda: collections.defaultdict(list))
    for r in rows:
        g, fam = r.get("tp_geometry"), r.get("family")
        if g is None or fam is None:
            continue
        by_level[str(g)][str(fam)].append(r)

    out: Dict[str, Any] = {}
    for level, fams in sorted(by_level.items()):
        per: Dict[str, Any] = {}
        for fam, sub in sorted(fams.items()):
            n = len(sub)
            c = sum(1 for r in sub if str(r.get("verdict")) == "candidate")
            per[fam] = {"candidate": c, "n": n, "rate": (c / n) if n else None}
        rates = [d["rate"] for d in per.values() if d["rate"] is not None]
        out[level] = {
            "families": per,
            "n_families": len(per),
            "spread": (max(rates) - min(rates)) if len(rates) >= 2 else None,
        }
    levels = [d for d in out.values() if d["spread"] is not None]
    biggest_within = max((d["spread"] for d in levels), default=None)

    all_rates = []
    for level, d in out.items():
        tot_n = sum(x["n"] for x in d["families"].values())
        tot_c = sum(x["candidate"] for x in d["families"].values())
        if tot_n:
            all_rates.append(tot_c / tot_n)
    between = (max(all_rates) - min(all_rates)) if len(all_rates) >= 2 else None

    return {
        "per_level": out,
        "largest_within_level_family_spread": biggest_within,
        "between_level_spread": between,
        "within_over_between": (
            (biggest_within / between)
            if (biggest_within is not None and between not in (None, 0.0))
            else None),
    }


def report(*, arms_path: str = DEFAULT_ARMS,
           extra_families: Sequence[str] = ()) -> Dict[str, Any]:
    prod = load_producers()
    rows, arms_state = load_arms(arms_path)

    fams: List[str] = sorted(prod["clamping_families"])
    seen = set(fams)
    if rows:
        for r in rows:
            f = r.get("family")
            if f and str(f) not in seen:
                fams.append(str(f))
                seen.add(str(f))
    for f in extra_families:
        if f not in seen:
            fams.append(f)
            seen.add(f)

    sign_test = cap_enters_only_as_a_sign_test(prod, fams)
    per_family = [family_reachability(prod, f) for f in fams]
    clause_a = grade_clause_a(per_family, sign_test)

    dose_families = [f["family"] for f in per_family
                     if f["dose_state"] == DOSE_REACHABLE]
    inert_families = [f["family"] for f in per_family
                      if f["dose_state"] == DOSE_INERT]

    out: Dict[str, Any] = {
        "clamping_families": sorted(prod["clamping_families"]),
        "venue_cap_pct": prod["venue_cap_pct"],
        "sign_test": sign_test,
        "per_family": per_family,
        "clause_a": clause_a,
        "dose_reachable_families": dose_families,
        "dose_inert_families": inert_families,
        "arms_state": arms_state,
    }
    if rows is None:
        out["census"] = None
        out["candidate_rate_by_geometry"] = None
        out["candidate_rate_by_family"] = None
        out["within_geometry_spread"] = None
    else:
        out["census"] = arm_census(prod, rows)
        out["candidate_rate_by_geometry"] = candidate_rate(rows, "tp_geometry")
        out["candidate_rate_by_family"] = candidate_rate(rows, "family")
        out["within_geometry_spread"] = within_geometry_spread(rows)
    return out


def render(v: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append("TP-GEOMETRY REACHABILITY — can the label vary inside one family?")
    L.append("")
    st = v["sign_test"]
    holds = st["holds"]
    L.append(f"  cap enters only as a sign test : {holds} "
             f"({st['families_graded']}/{st['families_offered']} families graded, "
             f"{len(st['magnitudes_tried'])} magnitudes)")
    if holds is not True:
        L.append("  ⚠️ THE SIGN TEST DOES NOT HOLD — every verdict below is a SAMPLE, "
                 "not a proof. Counterexamples: "
                 f"{st['counterexamples']}")
    L.append(f"  CLAMPING_FAMILIES              : {v['clamping_families']}")
    L.append("")
    L.append("  family            clamping  labels attainable                       "
             "pair            dose")
    for f in v["per_family"]:
        L.append(f"  {f['family']:<17} {str(f['in_clamping_families']):<9} "
                 f"{','.join(f['labels_attainable']):<39} "
                 f"{f['pair_state']:<15} {f['dose_state']}")
    L.append("")
    ca = v["clause_a"]
    L.append(f"  CLAUSE (A) — some family carries BOTH contrast levels: {ca['state']}")
    L.append(f"    {ca['why']}")
    L.append(f"  dose reachable on : {v['dose_reachable_families'] or '(none)'}")
    L.append(f"  dose INERT on     : {v['dose_inert_families'] or '(none)'}")

    c = v.get("census")
    L.append("")
    if c is None:
        L.append(f"  ARM CORPUS NOT GRADED — {v['arms_state']} (we did not look; "
                 "this is not evidence the corpus is clean)")
    else:
        L.append(f"  arm corpus: {c['arms']} arms · {c['legs']} legs · "
                 f"families {c['families']}  [{v['arms_state']}]")
        for k, n in c["combos"].items():
            L.append(f"    {k}: {n}")
        L.append(f"    families carrying ONE geometry only: "
                 f"{c['families_with_one_geometry_only']}")
        L.append(f"    declared vs delivered              : {c['declared_vs_delivered']}")
        if c["ungradeable"]:
            L.append(f"    ⚠️ ungradeable                     : {c['ungradeable']}")
        L.append("")
        for label, block in (("by tp_geometry", v["candidate_rate_by_geometry"]),
                             ("by family", v["candidate_rate_by_family"])):
            L.append(f"    candidate rate {label}:")
            for k, d in block.items():
                rate = "n/a" if d["rate"] is None else f"{d['rate']:.1%}"
                L.append(f"      {k:<24} {d['candidate']:>4}/{d['n']:<4} = {rate}")
        w = v.get("within_geometry_spread") or {}
        if w:
            L.append("")
            L.append("    FAMILY SPREAD *INSIDE* ONE GEOMETRY LEVEL — this bounds how "
                     "much the LEVEL can be carrying:")
            for level, d in sorted(w["per_level"].items()):
                sp = d["spread"]
                sp_s = "n/a (one family — no spread exists)" if sp is None else f"{sp:.1%}"
                L.append(f"      {level:<24} families={d['n_families']} spread={sp_s}")
                for fam, x in sorted(d["families"].items()):
                    r = "n/a" if x["rate"] is None else f"{x['rate']:.1%}"
                    L.append(f"        {fam:<20} {x['candidate']:>4}/{x['n']:<4} = {r}")
            bw, bt = w["largest_within_level_family_spread"], w["between_level_spread"]
            L.append(f"      largest WITHIN-level family spread : "
                     f"{'n/a' if bw is None else f'{bw:.1%}'}")
            L.append(f"      BETWEEN-level spread               : "
                     f"{'n/a' if bt is None else f'{bt:.1%}'}")
            wob = w["within_over_between"]
            L.append(f"      within / between                   : "
                     f"{'n/a' if wob is None else f'{wob:.2f}'}")
    return "\n".join(L)


# ---------------------------------------------------------------- self-test

def _self_test() -> int:
    passed = 0
    failed: List[str] = []

    def ck(name: str, cond: bool) -> None:
        nonlocal passed
        if cond:
            passed += 1
        else:
            failed.append(name)

    try:
        prod = load_producers()
    except ProducersUnavailable as exc:
        print(f"self-test: COULD NOT RUN — producers unavailable: {exc}")
        return 2

    geom = prod["tp_geometry_for"]

    # --- the producers behave as this module's whole argument requires -------
    ck("pullback is a clamping family", "pullback" in prod["clamping_families"])
    ck("scalp is NOT a clamping family", "scalp" not in prod["clamping_families"])
    ck("venue cap is positive", prod["venue_cap_pct"] > 0)
    ck("capped family at a positive cap is live_parity_capped",
       geom(["pullback"], 0.099) == "live_parity_capped")
    ck("capped family at cap 0 is NO_TAKE_PROFIT, not uncapped",
       geom(["pullback"], 0.0) == "NO_TAKE_PROFIT")
    ck("non-clamping family is uncapped at a positive cap",
       geom(["scalp"], 0.099) == "live_parity_uncapped")
    ck("non-clamping family is uncapped at cap 0 too",
       geom(["scalp"], 0.0) == "live_parity_uncapped")
    ck("an empty family set is its own state",
       geom([], 0.099) == "UNOBSERVED")
    ck("a mixed run refuses to pick a side",
       geom(["pullback", "scalp"], 0.099).startswith("MIXED"))

    # --- flag delivery is MEASURED off the real base_args -------------------
    ck("flag delivered for a clamping family at a positive cap",
       flag_delivered(prod, "pullback", 0.099) is True)
    ck("flag withheld for a clamping family at cap 0",
       flag_delivered(prod, "pullback", 0.0) is False)
    ck("flag withheld for a NON-clamping family even at a positive cap",
       flag_delivered(prod, "scalp", 0.099) is False)
    ck("flag delivery is a bool, never None, on a well-formed probe",
       isinstance(flag_delivered(prod, "donchian", 0.05), bool))

    # An argv builder that RAISES: "we could not look" must never be recorded as
    # "the flag was withheld". Without this control both collapse to False and a
    # census would count an unreadable probe as a delivered cap.
    def _boom(*_a, **_k):
        raise RuntimeError("argv builder unavailable")

    broken = dict(prod)
    broken["base_args"] = _boom
    ck("an unreadable argv build yields None, never False",
       flag_delivered(broken, "pullback", 0.099) is None)

    cen_unreadable = arm_census(broken, [
        {"leg": "q", "family": "pullback", "tp_geometry": "live_parity_capped",
         "tp_cap_pct": 0.099, "verdict": "candidate"}])
    ck("an unreadable delivery is ungradeable, not 'delivered'",
       cen_unreadable["ungradeable"].get("flag_delivery_unreadable") == 1)
    ck("an unreadable delivery enters NEITHER delivery bucket",
       cen_unreadable["declared_vs_delivered"] == {})

    fr_unreadable = family_reachability(broken, "pullback")
    ck("a family whose flag delivery is unreadable grades dose unknown",
       fr_unreadable["dose_state"] == DOSE_UNKNOWN)

    # --- the sign test, which is what upgrades sampling to proof ------------
    st = cap_enters_only_as_a_sign_test(prod, ["pullback", "scalp", "donchian"])
    ck("sign test holds on the real producer", st["holds"] is True)
    ck("sign test reports its denominator", st["families_graded"] == 3)
    ck("sign test tried more than two magnitudes", len(st["magnitudes_tried"]) > 2)
    ck("sign test names no counterexample when it holds", st["counterexamples"] == [])

    class _SizeSensitive:
        """A producer whose label DOES depend on the cap's magnitude."""

        @staticmethod
        def geom(fams, cap):
            if cap > 0.5:
                return "live_parity_uncapped"
            return geom(fams, cap)

    fake = dict(prod)
    fake["tp_geometry_for"] = _SizeSensitive.geom
    st_bad = cap_enters_only_as_a_sign_test(fake, ["pullback"])
    ck("a size-sensitive producer FAILS the sign test", st_bad["holds"] is False)
    ck("a failed sign test names its counterexample",
       st_bad["counterexamples"] and st_bad["counterexamples"][0]["family"] == "pullback")

    st_none = cap_enters_only_as_a_sign_test(prod, [])
    ck("no family offered => holds is None, never True", st_none["holds"] is None)

    # --- per-family reachability -------------------------------------------
    pull = family_reachability(prod, "pullback")
    ck("pullback attains capped only", pull["pair_state"] == PAIR_CAPPED_ONLY)
    ck("pullback's label DOES vary (capped vs NO_TAKE_PROFIT)",
       pull["label_varies"] is True)
    ck("pullback never attains live_parity_uncapped",
       "live_parity_uncapped" not in pull["labels_attainable"])
    ck("pullback's dose is reachable", pull["dose_state"] == DOSE_REACHABLE)

    sc = family_reachability(prod, "scalp")
    ck("scalp attains uncapped only", sc["pair_state"] == PAIR_UNCAPPED_ONLY)
    ck("scalp's label does NOT vary at all", sc["label_varies"] is False)
    ck("scalp's dose is INERT — the flag never reaches its harness",
       sc["dose_state"] == DOSE_INERT)

    # --- clause grading -----------------------------------------------------
    ca = grade_clause_a([pull, sc], st)
    ck("clause A is unsatisfiable by construction",
       ca["state"] == CLAUSE_UNSATISFIABLE)
    ck("clause A names no satisfying family", ca["families_with_both"] == [])

    ca_unknown = grade_clause_a([pull, sc], {"holds": None})
    ck("clause A degrades to unknown when the sign test did not hold",
       ca_unknown["state"] == CLAUSE_UNKNOWN)

    ca_empty = grade_clause_a([], st)
    ck("clause A on no families is unknown, never unsatisfiable",
       ca_empty["state"] == CLAUSE_UNKNOWN)

    both = {"family": "imaginary", "pair_state": PAIR_BOTH}
    ck("clause A is satisfiable when a family attains both",
       grade_clause_a([both, pull], st)["state"] == CLAUSE_SATISFIABLE)

    unk = {"family": "u", "pair_state": PAIR_UNKNOWN}
    ck("one ungradeable family blocks an absence claim",
       grade_clause_a([pull, unk], st)["state"] == CLAUSE_UNKNOWN)

    # --- corpus census ------------------------------------------------------
    fake_rows = [
        {"leg": "a", "family": "pullback", "block_unit": "family_pooled",
         "tp_geometry": "live_parity_capped", "tp_cap_pct": 0.099,
         "verdict": "candidate"},
        {"leg": "b", "family": "pullback", "block_unit": "family_pooled",
         "tp_geometry": "live_parity_capped", "tp_cap_pct": 0.099,
         "verdict": "honest_negative"},
        {"leg": "c", "family": "scalp", "block_unit": "per_leg",
         "tp_geometry": "live_parity_uncapped", "tp_cap_pct": 0.099,
         "verdict": "candidate"},
    ]
    cen = arm_census(prod, fake_rows)
    ck("census counts arms", cen["arms"] == 3)
    ck("census counts legs", cen["legs"] == 3)
    ck("census finds every family carries one geometry only",
       set(cen["families_with_one_geometry_only"]) == {"pullback", "scalp"})
    ck("census separates delivered from recorded-but-never-delivered",
       cen["declared_vs_delivered"] == {"delivered": 2,
                                        "recorded_but_never_delivered": 1})

    cen_bad = arm_census(prod, [{"leg": "x", "family": "scalp",
                                 "tp_geometry": "live_parity_uncapped",
                                 "tp_cap_pct": "oops", "verdict": "candidate"}])
    ck("a non-numeric cap is ungradeable, not delivered",
       cen_bad["ungradeable"].get("non_numeric_tp_cap_pct") == 1)
    ck("a non-numeric cap contributes to NEITHER delivery bucket",
       cen_bad["declared_vs_delivered"] == {})

    cen_missing = arm_census(prod, [{"leg": "y", "tp_cap_pct": 0.099}])
    ck("a row with no family is ungradeable",
       cen_missing["ungradeable"].get("missing_geometry_or_family") == 1)

    # --- rates state their denominators ------------------------------------
    rate = candidate_rate(fake_rows, "tp_geometry")
    ck("rate states its denominator",
       rate["live_parity_capped"]["n"] == 2 and rate["live_parity_capped"]["candidate"] == 1)
    ck("rate is a fraction, not a count",
       abs(rate["live_parity_uncapped"]["rate"] - 1.0) < 1e-12)
    ck("an empty bucket yields no row", "nope" not in rate)

    # --- within-level family spread bounds the attribution ------------------
    spread_rows = [
        {"leg": "a", "family": "donchian", "tp_geometry": "live_parity_capped",
         "tp_cap_pct": 0.099, "verdict": "candidate"},
        {"leg": "b", "family": "donchian", "tp_geometry": "live_parity_capped",
         "tp_cap_pct": 0.099, "verdict": "honest_negative"},
        {"leg": "c", "family": "pullback", "tp_geometry": "live_parity_capped",
         "tp_cap_pct": 0.099, "verdict": "honest_negative"},
        {"leg": "d", "family": "pullback", "tp_geometry": "live_parity_capped",
         "tp_cap_pct": 0.099, "verdict": "honest_negative"},
        {"leg": "e", "family": "scalp", "tp_geometry": "live_parity_uncapped",
         "tp_cap_pct": 0.099, "verdict": "candidate"},
    ]
    w = within_geometry_spread(spread_rows)
    ck("within-level spread is computed for a level with two families",
       abs(w["per_level"]["live_parity_capped"]["spread"] - 0.5) < 1e-12)
    ck("a one-family level has spread None, never 0.0",
       w["per_level"]["live_parity_uncapped"]["spread"] is None)
    ck("between-level spread uses the pooled level rate",
       abs(w["between_level_spread"] - 0.75) < 1e-12)
    ck("largest within-level spread is reported",
       abs(w["largest_within_level_family_spread"] - 0.5) < 1e-12)
    ck("within/between is a ratio of the two",
       abs(w["within_over_between"] - (0.5 / 0.75)) < 1e-12)
    ck("within-level spread names each family's own denominator",
       w["per_level"]["live_parity_capped"]["families"]["pullback"]["n"] == 2)

    # Two levels whose POOLED rates are identical: the ratio's denominator is
    # exactly 0.0, which must yield None rather than raising. A guard that only
    # tests `is not None` passes every ordinary fixture and dies on this one.
    tied = [
        {"leg": "t1", "family": "donchian", "tp_geometry": "live_parity_capped",
         "tp_cap_pct": 0.099, "verdict": "candidate"},
        {"leg": "t2", "family": "pullback", "tp_geometry": "live_parity_capped",
         "tp_cap_pct": 0.099, "verdict": "honest_negative"},
        {"leg": "t3", "family": "scalp", "tp_geometry": "live_parity_uncapped",
         "tp_cap_pct": 0.099, "verdict": "candidate"},
        {"leg": "t4", "family": "scalp", "tp_geometry": "live_parity_uncapped",
         "tp_cap_pct": 0.099, "verdict": "honest_negative"},
    ]
    w_tied = within_geometry_spread(tied)
    ck("a zero between-level spread is reported as 0.0",
       w_tied["between_level_spread"] == 0.0)
    ck("within/between on a zero denominator is None, never a crash",
       w_tied["within_over_between"] is None)

    w_one = within_geometry_spread([spread_rows[4]])
    ck("a single level yields no between-level spread",
       w_one["between_level_spread"] is None)
    ck("a single level yields no within/between ratio",
       w_one["within_over_between"] is None)
    ck("a row missing its family is skipped, not counted",
       within_geometry_spread([{"leg": "z", "tp_geometry": "x"}])["per_level"] == {})

    # --- absent corpus is 'we did not look', never a clean negative ---------
    rows_none, why = load_arms("/nonexistent/arms.jsonl")
    ck("an absent corpus reads None", rows_none is None)
    ck("an absent corpus explains itself", why.startswith("absent:"))

    rep = report(arms_path="/nonexistent/arms.jsonl")
    ck("report with no corpus still grades reachability",
       rep["clause_a"]["state"] == CLAUSE_UNSATISFIABLE)
    ck("report with no corpus publishes NO census", rep["census"] is None)
    ck("report with no corpus says so in words",
       "ARM CORPUS NOT GRADED" in render(rep))

    # --- render never crashes on a degraded read ----------------------------
    ck("render works on a degraded sign test",
       "SAMPLE" in render({**rep, "sign_test": {**rep["sign_test"], "holds": None,
                                                "counterexamples": []}}))

    random.seed(0)
    mags = [random.uniform(1e-6, 1e6) for _ in range(40)]
    st_rand = cap_enters_only_as_a_sign_test(prod, ["pullback", "scalp"],
                                             magnitudes=mags)
    ck("sign test holds over 40 random positive magnitudes",
       st_rand["holds"] is True)

    total = passed + len(failed)
    if failed:
        print(f"self-test: {passed}/{total} PASS")
        for f in failed:
            print(f"  FAIL: {f}")
        return 1
    print(f"self-test: {passed}/{total} PASS")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arms", default=DEFAULT_ARMS,
                    help="consolidated fold-dispersion arm set (JSONL)")
    ap.add_argument("--family", action="append", default=[],
                    help="grade an extra family not present in the corpus")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    try:
        v = report(arms_path=a.arms, extra_families=a.family)
    except ProducersUnavailable as exc:
        print("REFUSING TO GRADE — the real producers could not be imported, and a "
              "local re-derivation of their rule would be exactly the drift this "
              f"module exists to detect.\n  cause: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(v, indent=2, sort_keys=True) if a.json else render(v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
