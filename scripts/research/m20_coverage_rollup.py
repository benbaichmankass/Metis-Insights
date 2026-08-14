#!/usr/bin/env python3
"""M20 exit-refinement coverage roll-up — the ONE place the milestone's
done-condition is computed.

WHY THIS EXISTS
---------------
``docs/research/exit-refinement-coverage.json`` IS the contract (per
``.claude/skills/exit-refinement``), and it is in good shape: every closed
live cell carries an evidence ref. What was never computed anywhere is its
**headline**. Each session that quoted a coverage number hand-counted it, and
the counts diverged over a population that had not changed:

===========================================  ===========
source                                       figure
===========================================  ===========
PR #8712 (2026-08-10, the last matrix write) ``319 / 376``
2026-08-12 continuation prompt               ``304 / 376``
fresh hand-count, same file, same day        ``311 / 376``
===========================================  ===========

Three numbers for one file. Worse, the 304 figure was **internally
inconsistent with its own second sentence**: the same prompt said "57 pending
cells", and 376 − 57 = 319, not 304. A reader had no way to see that without
recomputing, because neither number stated its derivation.

That is CLAUDE.md § "Diagnostic provenance" sub-class **A** — a value printed
under a label that does not describe what was counted. The fix is the same one
that section prescribes everywhere else: compute it in one place, and make the
output state its own population.

THE DIVERGENCE IS NOT ARITHMETIC — "CLOSED" HAS THREE DEFENSIBLE MEANINGS
------------------------------------------------------------------------
The seven legend statuses do not sort cleanly into done/not-done, and no
session stated which cut it used:

* ``shipped`` / ``honest_negative`` / ``n/a`` — uncontroversially resolved.
* ``passed_unshipped`` / ``shipped_gate_failed`` — **validated**; the research
  is complete and a decision is recorded. Counting these as open would say the
  lever is unprocessed, which is false.
* ``blocked:<reason>`` — processed as far as it *can* be. Whether this is
  "closed" is the actual judgment call, and it is the one that moves the
  number most.

So this script reports all three cuts, names which is authoritative for the
progress headline, and — the part that matters — reports them **separately**
from the done-condition, because those two are not the same question.

⚠️ THE HEADLINE AND THE DONE-CONDITION MEASURE DIFFERENT THINGS, DELIBERATELY
----------------------------------------------------------------------------
This is the trap the divergence was hiding, and it survives the fix:

* The **progress headline** counts ``blocked`` as closed. A blocked cell is not
  work anyone can do — it is gated on data that does not exist — so leaving it
  in the numerator's complement would make the percentage a measure of the
  data backlog rather than of exit-refinement progress.
* The **done-condition** does not. The skill states it exactly:
  *"The milestone/health view of 'are we done' = no ``pending``/``blocked``
  rows on live legs."*

So M20's done-condition is the ``pending`` cells PLUS the ``blocked`` ones. A
session reading only the pending count will under-scope the milestone and,
more importantly, will never revisit the blocked ones, which is how a
``blocked:data_missing`` row becomes permanent. ``--done-condition`` prints
that set explicitly.

⚠️ **EVERY FIGURE IN THIS DOCSTRING IS HISTORICAL.** The tables and counts above
record the 2026-08-12 divergence that motivated the script; they are NOT the
current state and must not be quoted as it. On 2026-08-13 the headline reached
``360/360 = 100.0%`` with a done-condition of ``37`` still open — the two have
separated as far as they arithmetically can, which makes quoting a stale
headline maximally misleading. **Run the script.** It prints its own population
line, which is the only figure that is true by construction.

POPULATION
----------
Denominator is **live legs only** × the declared ``lever_columns``, COMPUTED at
run time and printed on the ``population:`` line — do not hard-code it here (an
earlier version of this paragraph said ``47 × 8 = 376`` and went stale when the
live roster changed to 45 legs = 360 cells). "Live" is the leg's EFFECTIVE
state: ``enabled: false`` outranks any ``execution:`` value, because a disabled
leg is never constructed and so cannot execute in any mode — see
``_effective_execution``. Shadow and disabled legs are excluded from
every figure here, matching the matrix's own framing; they are still
*validated* (a null status is an error wherever it appears) and reported under
``--validate`` so an excluded row cannot rot unnoticed.

Tier-1 research tooling. Reads the matrix; writes nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"

# The three cuts, widest-numerator last. `blocked` is deliberately the only
# difference between HEADLINE and DONE — see the module docstring.
RESOLVED = ("shipped", "honest_negative", "n/a")
VALIDATED = RESOLVED + ("passed_unshipped", "shipped_gate_failed")
HEADLINE = VALIDATED + ("blocked",)

# Statuses that still owe work. `blocked` owes work that is gated elsewhere
# (data, harness) rather than on an exit sweep — hence its own bucket.
OPEN_STATUSES = ("pending", "blocked")

# ---------------------------------------------------------------- vintage
#
# BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP (open, severity
# critical) established that three live units clamp their 50R "sentinel" TP to
# 9.9% from entry — `tp = min(entry * 1.099, entry + tp_r * risk)` — because
# Bybit rejects a TP further out, while their harnesses modelled NO take-profit
# at all. At the ATR levels these legs actually run, that live TP sits at
# ~1.3-5R: an ordinary, frequently-touched target, not a sentinel. A trail lever
# tuned where the trail is the SOLE exit is not the same lever where a hard TP
# truncates the right tail first.
#
# The backlog row states the consequence plainly: it "sits underneath M20's
# honest_negatives". So the coverage headline is computed over a population
# whose verdicts were largely measured on a book production does not run — and
# NOTHING in the matrix says so (measured 2026-08-12: 0 of 396 non-empty refs
# mention the geometry). That is the "always state the population" rule applied
# to the milestone's own progress metric.
#
# WHICH LEGS — verified 2026-08-12 by reading each live unit against its
# harness, NOT inherited from the backlog row's prose:
#
#   AFFECTED   `_TP_SENTINEL_CAP_PCT` in the live unit + no TP in the harness
#              trend_donchian.py · htf_pullback_trend_2h.py · squeeze_breakout_4h.py
#              (fade_breakout_4h.py also carries the cap but has no live leg)
#   CLEAN      ict_scalp — live places `tp = entry ± tp_at_r * risk` (no cap) and
#              backtest_ict_scalp.py MODELS it (`bar_high >= tp -> tp_hit`)
#   CLEAN      fvg_range_15m — live `tp = R` (the opposite range boundary), a real
#              bounded target; its harness takes `--tp-r`
#
# So this is NOT a blanket "every verdict is suspect" — scoping it to the three
# families is the difference between a usable caveat and alarm fatigue.
GEOMETRY_CUTOVER = "2026-08-10"
TP_PARITY_AFFECTED_FAMILIES = frozenset({"donchian", "pullback", "squeeze"})

# ONE CUTOVER WAS A WRONG ANSWER SHAPED LIKE A RIGHT ONE
# (added 2026-08-14, BL-20260814-EXIT-HEAD-ROUNDS-CANNOT-MODEL-LIVE-TP).
#
# `GEOMETRY_CUTOVER` above is the date the LEVER-SWEEP harness learned to place
# the live capped TP. `exit_head_ml` does not ride that harness — it rides
# `m20_exit_head_round.py`, which called `base_args(...)` with five positional
# args so `tp_cap_pct` took its default `0.0`, AND whose argparse had no
# `--tp-cap-pct` option at all. No caller could request live parity, so EVERY
# round built a no-take-profit book until the driver was fixed on 2026-08-14.
#
# A single scalar therefore graded `exit_head_ml` cells against a date four days
# too early, and the cells it wrongly cleared are the expensive kind: the three
# SHIPPED `trend_donchian*` 1h cells carry refs dated 2026-08-13, so they cleared
# a 2026-08-10 bar while resting on a round measured to contain ZERO take-profit
# exits (full populations, n=938..1992). `--stale-decisions` listed five cells and
# read as the complete set of money-relevant cells on wrong-geometry evidence;
# three real-money ones sat outside it because the only test was a date, and the
# date was the wrong harness's.
#
# A lever absent from this map falls back to `GEOMETRY_CUTOVER` — the default is
# the common case, and a new lever cannot silently acquire a later bar than its
# evidence deserves.
LEVER_GEOMETRY_CUTOVER = {
    # the day `m20_exit_head_round.py` gained `--tp-cap-pct` (live parity as the
    # DEFAULT) and began stamping `_round_meta.tp_geometry` into round_report.json
    "exit_head_ml": "2026-08-14",
}


def cutover_for(lever: str) -> str:
    """The date THIS lever's harness started modelling the live TP."""
    return LEVER_GEOMETRY_CUTOVER.get(lever, GEOMETRY_CUTOVER)


# A DATE IS A PROXY, AND IT HAS ALREADY FAILED ONCE (2026-08-14).
#
# The three SHIPPED `trend_donchian*` 1h `exit_head_ml` cells carry a genuine
# `RE-SWEPT 2026-08-14` ref — a real measurement, on that date — and are still
# built on a NO-TAKE-PROFIT book, because that re-sweep re-read the EXISTING
# round dirs rather than building new ones, and every round on disk predates the
# driver fix. So the cell is fresh by date and stale by geometry at the same
# time, and no choice of cutover date can separate those: the property that
# matters is not WHEN the cell was measured but WHETHER the round behind it
# placed the live capped TP.
#
# `tp_geometry` is that property, stated on the cell, and it OVERRIDES the date
# in both directions. Three states, never collapsed:
#
#   "no_take_profit"  the round behind this cell was MEASURED to contain zero
#                     take-profit exits -> stale whatever the date says
#   "live_parity"     the round declares `_round_meta.tp_geometry: live_parity`
#                     -> not stale whatever the date says
#   absent            unknown; fall back to the date. Counted in
#                     `geometry_undeclared` so "we did not look" stays visible
#                     rather than reading as a clean pass.
#
# This is a DECLARED MEASUREMENT, not a presence-only marker: the eleven rounds
# were each read end-to-end (full populations, n=100..1992, zero TP exits) before
# any cell was stamped, and each round carries its own evidence on disk in
# `GEOMETRY-NO-TAKE-PROFIT.txt`.
GEOMETRY_NO_TP = "no_take_profit"
GEOMETRY_LIVE_PARITY = "live_parity"

# Levers whose verdict is a claim about the exit path, and so is conditioned on
# the TP geometry the harness modelled. `exit_head_ml` is included: its E0 rows
# come from the same `--emit-trades` harness paths.
#
# ⚠️ THIS CURRENTLY CONTAINS EVERY DECLARED LEVER — it filters NOTHING today,
# and saying so is the point. Every one of the eight columns is a claim about
# the exit path, so the honest scoping happens on the FAMILY axis, not this one.
# The set exists so that a future non-exit column (an entry-side lever, say)
# cannot be swept into the geometry caveat by default. A reader comparing the
# vintage denominator against the lever list should find them equal; if they
# ever diverge, this is the line that explains why.
GEOMETRY_SENSITIVE_LEVERS = frozenset({
    "trail_geometry", "stale_stop", "giveback_stop", "trail_decay",
    "vol_trail", "exit_ladder", "regime_flip_exit", "exit_head_ml",
})

_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")

# A NOTE ABOUT THE EVIDENCE IS NOT THE EVIDENCE BEING RENEWED.
#
# The date proxy asks "how old is this cell's newest ref date?", which silently
# assumes every date in a ref marks a MEASUREMENT. Some segments are commentary
# — an annotation recording that OTHER evidence exists, or that a re-sweep was
# attempted and returned nothing. Their dates are the date the NOTE was written.
#
# Caught 2026-08-14 on my own diff, before merge, by the three-state report added
# in this same commit: annotating 8 cells with live-parity counter-evidence
# stamped "2026-08-14" into their refs, and all 8 promptly DROPPED OUT of
# `stale_cells` — un-staled by a note, with no new measurement behind it. The
# cells that already handled this (mhg/mes) survive only because they carry an
# explicit `tp_geometry: no_take_profit`, which overrides the proxy; a cell with
# `tp_geometry: null` had nothing but the date and was silently laundered.
#
# The matrix's own refs state the rule in prose — "a note about the evidence is
# not the evidence being renewed" — so this encodes what was already written
# down and merely unenforced.
_ANNOTATION_SEGMENT = re.compile(
    r"LIVE-PARITY COUNTER-EVIDENCE ALREADY IN THE CORPUS"
    r"|RE-SWEEP ATTEMPTED .{0,40}AND RETURNED NO VERDICT",
    re.I)


def evidence_dates(ref: str | None) -> list[str]:
    """Dates from the EVIDENCE portion of a ref, excluding annotation segments.

    Refs are `||`-separated. A segment that is commentary about the evidence
    (rather than a measurement) contributes no date, so writing a note can never
    make a cell look freshly measured.
    """
    if not ref:
        return []
    keep = [seg for seg in ref.split("||")
            if not _ANNOTATION_SEGMENT.search(seg)]
    return _DATE.findall("||".join(keep))


def _declared_strategies() -> set[str] | None:
    """Leg names declared in config/strategies.yaml, or None if unreadable.

    None is a THIRD state the caller reports as "not validated" — never as
    "all names are fine". An unreadable config must not read as a clean pass.
    """
    try:
        import yaml
        cfg = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
    except (ImportError, OSError, ValueError):
        return None
    strategies = (cfg or {}).get("strategies")
    return set(strategies) if isinstance(strategies, dict) else None


def _declared_legs() -> dict[str, dict[str, Any]] | None:
    """Leg name -> its declared body from config/strategies.yaml, or None.

    The richer sibling of `_declared_strategies`, which returns only the name
    SET and therefore cannot answer the question the reverse-direction check
    needs: *is this leg live?* Both resolve `execution` the same way the
    runtime does — **omitted means `live`** (the two-gates rule: both gates are
    default-permissive, so a leg is demoted only by an explicit `shadow`).
    Reading an absent `execution` as anything other than `live` here would make
    the guard quietly exempt exactly the legs it exists to police.

    None on an unreadable config — the same third state as `_declared_strategies`,
    reported by the caller as "not validated", never as "clean".
    """
    try:
        import yaml
        cfg = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
    except (ImportError, OSError, ValueError):
        return None
    strategies = (cfg or {}).get("strategies")
    if not isinstance(strategies, dict):
        return None
    return {name: (body or {}) for name, body in strategies.items()}


def _effective_execution(body: dict[str, Any]) -> str:
    """The leg's EFFECTIVE state — `live` | `shadow` | `disabled`.

    TWO config keys collapse into the one value the matrix's `execution` column
    carries, and the order between them is the whole point: `enabled: false`
    wins over any `execution:`, because a disabled leg is never constructed at
    all and so cannot execute in any mode.

    This is not hypothetical tidiness. `xauusd_trend_1h` declares
    `enabled: false` WITH `execution: live`, and the live trader's loaded-strategy
    list (52 legs, read 2026-08-13T08:14Z) does not contain it. An earlier draft
    of the join check compared the raw `execution` strings and reported the
    matrix's correct `disabled` as a defect — a guard manufacturing a false
    positive against a row that was right. Both keys, in this order, or the
    comparison is meaningless.

    Defaults are permissive on both keys (the two-gates rule): a leg is demoted
    only by saying so explicitly.
    """
    if body.get("enabled", True) is False:
        return "disabled"
    return body.get("execution", "live")


def _is_live(body: dict[str, Any]) -> bool:
    """Effective liveness — the population the M20 denominator counts."""
    return _effective_execution(body) == "live"


def _family_of(strategy: str) -> str | None:
    """Resolve a leg's harness family via the sweep's own `classify`.

    Imported, never re-implemented: a second copy of the family split would be
    free to drift from the one that actually picks the harness, and the failure
    it produces (a leg attributed to the wrong family) looks like a clean
    result rather than a bug. Returns None when the import is unavailable —
    a third state the caller reports rather than guessing around.
    """
    try:
        sys.path.insert(0, str(REPO / "scripts" / "research"))
        from m20_fleet_exit_sweep import classify
    except ImportError:
        # ONLY an import failure is tolerated, and it is reported (the caller
        # sets `classifier_available: False`), never treated as "no staleness".
        # A broad `except Exception` here would also swallow a `classify()`
        # crash on a real leg name, which would silently mark the WHOLE fleet
        # unclassifiable and make the caveat vanish — the failure looking like
        # a clean result, which is the thing this file exists to stop.
        return None
    return classify(strategy)


def evidence_vintage(matrix: dict[str, Any]) -> dict[str, Any]:
    """How much of the closed population predates the TP-parity cutover."""
    out = {
        # `cutover` is the DEFAULT bar, not the only one. Reporting a single
        # scalar under a name that reads as "the cutover" is the label-names-a-
        # quantity-the-code-did-not-compute shape; `cutovers` carries the per-
        # lever overrides so a reader can see why a 2026-08-13 cell is stale.
        "cutover": GEOMETRY_CUTOVER,
        "cutovers": dict(LEVER_GEOMETRY_CUTOVER),
        "affected_families": sorted(TP_PARITY_AFFECTED_FAMILIES),
        "classifier_available": True,
        "pre_cutover": 0, "post_cutover": 0, "undated": 0,
        # Cells with no `tp_geometry` field, graded by the date proxy alone.
        # Reported so "we did not look" never reads as "we looked and it's fine".
        "geometry_undeclared": 0,
        "affected_legs": 0, "clean_legs": 0,
        "stale_cells": [],
        # THE SPLIT THAT DECIDES WHAT THE CAVEAT COSTS. A stale NEGATIVE costs
        # knowledge — the lever might have passed and we would not know. A stale
        # SHIPPED costs money: it is changing exit behaviour on a real-money leg
        # right now, on a number measured against a geometry production does not
        # run. Reporting one aggregate for both invites the reader to price the
        # whole caveat at the cost of the cheap half, which is what happened:
        # the caveat shipped 2026-08-12 and it took a separate hand-audit on
        # 2026-08-13 to notice that 19 of the stale cells were live decisions.
        #
        # DO NOT HARDCODE A RE-SWEEP BASE RATE HERE. This comment used to read
        # "Base rate so far is 1 of 1: `trend_donchian` `trail_decay` ... did
        # NOT reproduce (now `shipped_gate_failed`)", and the printed banner
        # said the same. Both went stale and stayed confident: measured
        # 2026-08-14, that cell's status is `shipped` (NOT
        # `shipped_gate_failed`, which appears zero times in the matrix today)
        # and the lever PASSES -- the 2026-08-12 "did not reproduce" reading
        # was itself invalid, because that sweep measured the base against
        # itself (BL-20260813-SWEEP-GRADES-SHIPPED-LEVERS-AGAINST-THEMSELVES).
        # A rate baked into printed text is a claim nothing re-derives; read it
        # from docs/research/m20-sweep-corpus.jsonl instead.
        "stale_decisions": [],
    }
    for row in matrix["rows"]:
        if row.get("execution") != "live":
            continue
        fam = _family_of(row["strategy"])
        if fam is None:
            out["classifier_available"] = False
            return out
        if fam not in TP_PARITY_AFFECTED_FAMILIES:
            out["clean_legs"] += 1
            continue
        out["affected_legs"] += 1
        for col in matrix["lever_columns"]:
            if col not in GEOMETRY_SENSITIVE_LEVERS:
                continue
            cell = row.get(col) or {}
            if base(cell.get("status")) in OPEN_STATUSES + ("MISSING",):
                continue  # an open cell owes a measurement anyway
            if base(cell.get("status")) == "n/a":
                continue  # structurally inapplicable — no measurement to age
            # evidence_dates(), not _DATE.findall(): an annotation
            # segment must not renew a cell's vintage. See the comment
            # on _ANNOTATION_SEGMENT.
            dates = evidence_dates(cell.get("ref"))
            # PER-LEVER, not one global date: `exit_head_ml` rides a different
            # harness whose TP fix landed four days later (see the map above).
            cut = cutover_for(col)
            geom = cell.get("tp_geometry")
            if geom == GEOMETRY_NO_TP:
                stale = True          # measured; the date cannot overrule it
            elif geom == GEOMETRY_LIVE_PARITY:
                stale = False         # declared by the round itself
            else:
                out["geometry_undeclared"] += 1
                stale = (not dates) or max(dates) < cut
            if not dates:
                out["undated"] += 1
            elif stale:
                out["pre_cutover"] += 1
                out["stale_cells"].append(
                    (row["strategy"], row["symbol"], row["tf"], col,
                     max(dates), cut))
            else:
                out["post_cutover"] += 1
            # A stale cell that is not a negative is a live DECISION resting on
            # unreproduced evidence. Keyed on "not honest_negative" rather than
            # on a shipped-list, so a status added to the legend later cannot
            # silently fall out of this set.
            if stale and base(cell.get("status")) != "honest_negative":
                out["stale_decisions"].append(
                    (row["strategy"], col, cell.get("status"),
                     max(dates) if dates else None, cut))
    return out


def base(status: str | None) -> str:
    """`blocked:data_missing` -> `blocked`. None stays None (an error)."""
    return status.split(":", 1)[0] if isinstance(status, str) else status


# THREE STATES, BECAUSE "STALE" ALONE IS THE WRONG PROMPT.
# `stale_cells` says the evidence is OLD. It does not say whether NEWER evidence
# already exists, so it sends a reader to re-run a sweep that may already have
# been run. Measured 2026-08-14
# (BL-20260814-STALE-CELL-BACKLOG-IS-HALF-ANSWERED-BY-THE-CORPUS-ALREADY): of 186
# stale cells, 100 already had a live-parity corpus row and 9 of those PASSED
# against a recorded negative. Over half the backlog was a JOIN, not a re-run.
CORPUS_NO_ROW = "no_live_parity_row"       # nothing newer — a re-run IS the remedy
CORPUS_AGREES = "live_parity_agrees"       # newer evidence exists and matches
CORPUS_DISAGREES = "live_parity_disagrees"  # newer evidence exists and contradicts
CORPUS_UNAVAILABLE = "corpus_unavailable"   # we could not look — NOT "no row"


def _corpus_resolver():
    """The ONE definition of "newest floor-clearing live-parity row".

    Loaded from the guard rather than re-implemented here. Two copies of this
    predicate would be free to drift, and the drift would be silent: the roll-up
    would report a state the guard does not enforce, or vice versa. The repo has
    a standing rule against re-deriving a vocabulary that already has a home
    (see the `provenance` and `_regime_score_semantics` modules), and this is the
    same shape one directory over.
    """
    import importlib.util
    guard = REPO / "scripts" / "ci" / "check_matrix_corpus_agreement.py"
    if not guard.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_mca", guard)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def stale_corpus_state(matrix: dict[str, Any]) -> dict[str, Any]:
    """Per stale cell: does the corpus already answer it, and how?

    Returns the three states above, never collapsing "we could not look" into
    "there is no row" — those are opposite claims and the second is the one that
    sends someone to burn a sweep.
    """
    out = {"available": True, "counts": {}, "rows": []}
    mod = _corpus_resolver()
    corpus = REPO / "docs" / "research" / "m20-sweep-corpus.jsonl"
    if mod is None or not corpus.is_file():
        out["available"] = False
        out["why"] = (f"resolver_present={mod is not None} "
                      f"corpus_present={corpus.is_file()}")
        return out
    rows = [json.loads(x) for x in corpus.read_text().splitlines() if x.strip()]
    v = evidence_vintage(matrix)
    for leg, symbol, tf, lever, ref_date, cut in v.get("stale_cells", []):
        hit = mod.newest_floor_clearing_pass(rows, leg, lever)
        cell = next((r.get(lever) or {} for r in matrix["rows"]
                     if r["strategy"] == leg), {})
        status = cell.get("status")
        if hit is None:
            state = CORPUS_NO_ROW
        elif base(status) in mod.NEGATIVE_STATUSES:
            state = CORPUS_DISAGREES
        else:
            state = CORPUS_AGREES
        out["counts"][state] = out["counts"].get(state, 0) + 1
        out["rows"].append({
            "leg": leg, "symbol": symbol, "tf": tf, "lever": lever,
            "status": status, "state": state,
            "corpus_cell": (hit or {}).get("cell"),
            "corpus_verdict": (hit or {}).get("verdict"),
            "corpus_run": ((hit or {}).get("run_id") or "")[:10] or None,
            "corpus_base_oos": (hit or {}).get("base_trades_OOS"),
        })
    return out


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def cells(matrix: dict[str, Any], live_only: bool = True):
    """Yield (row, column, raw_status). The population, in one place."""
    for row in matrix["rows"]:
        if live_only and row.get("execution") != "live":
            continue
        for col in matrix["lever_columns"]:
            cell = row.get(col)
            status = cell.get("status") if isinstance(cell, dict) else None
            yield row, col, status


def validate(matrix: dict[str, Any]) -> list[str]:
    """Structural checks. Runs over EVERY row, live or not.

    A shadow row's null status is still a defect: the row is one promotion
    away from being live, and a status of `null` is not in the legend — it is
    the absence of a verdict wearing a verdict's shape.
    """
    problems: list[str] = []
    legend = set(matrix.get("legend") or {})
    if not legend:
        problems.append("legend is empty — cannot validate statuses")

    # EVERY LIVE LEG MUST RESOLVE IN config/strategies.yaml.
    #
    # Found 2026-08-12: 7 of 47 live rows were keyed `spy_trend_1d`,
    # `qqq_trend_1d`, … while config declares `spy_trend_long_1d` etc. — the
    # `_long` infix was missing. That is not a cosmetic key mismatch. Every
    # tool that acts on a matrix row resolves it against strategies.yaml, and
    # `m20-exit-lever-sweep`'s plan job FAILS on an unknown leg by design
    # (an input that is declared but silently ignored is worse than no input),
    # so those 7 legs could not be swept AT ALL — which is the most likely
    # reason their cells sat `pending` while neighbours were processed.
    #
    # A row naming a leg that does not exist also inflates the denominator:
    # the milestone counts it, and no work can ever close it.
    declared = _declared_strategies()
    if declared is None:
        problems.append(
            "config/strategies.yaml could not be read — leg names NOT "
            "validated (this is 'unchecked', not 'clean')")
    else:
        for row in matrix["rows"]:
            if row.get("execution") != "live":
                continue
            if row.get("strategy") not in declared:
                problems.append(
                    f"{row.get('strategy')}/{row.get('symbol')}/{row.get('tf')}: "
                    "live leg is not declared in config/strategies.yaml — no "
                    "sweep can resolve it, so its cells can never be closed")

    # ── THE REVERSE DIRECTION: every LIVE config leg must HAVE a matrix row ──
    #
    # The check above walks matrix -> config and catches a row naming a leg that
    # does not exist (a denominator that is too BIG). It cannot catch the
    # opposite and more dangerous error: a live, harness-classified leg with no
    # row at all — a denominator that is too SMALL. Nothing counts that leg, no
    # cell is ever opened for it, and the milestone's percentage is computed
    # over a population that silently excludes it.
    #
    # That matters precisely now: M20's headline reached `360/360 = 100.0%` on
    # 2026-08-13. A 100% over an under-counted denominator is the most
    # expensive shape this file can produce, because it reads as finished.
    # `BL-20260810-COVERAGE-MATRIX-LEG-IDS-DO-NOT-JOIN-TO-CONFIG` asked for the
    # set-difference to be empty **in both directions, enforced by a guard
    # rather than by inspection** — only one direction was ever enforced.
    #
    # Measured when this was added: 45 live harness-classified legs in config,
    # 45 live rows in the matrix, both differences empty. So this ships GREEN —
    # it locks in a convergence that already holds rather than reporting a new
    # defect, and the self-test is what proves the probe can still find a
    # positive (a green guard that cannot fail is not evidence).
    legs = _declared_legs()
    if legs is None:
        problems.append(
            "config/strategies.yaml could not be read — the config->matrix "
            "direction was NOT checked (this is 'unchecked', not 'clean')")
    else:
        rows_by_leg: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in matrix["rows"]:
            rows_by_leg[row.get("strategy")].append(row)

        # A leg with TWO rows is the failure mode the bundled-row note warns
        # about: the matrix asserts two independent statuses for one leg and a
        # reader gets whichever they looked up first.
        for leg, dup in sorted(rows_by_leg.items()):
            if len(dup) > 1:
                problems.append(
                    f"{leg}: {len(dup)} matrix rows for ONE leg — the matrix "
                    "can assert two different statuses for the same leg "
                    "depending on which row is read")

        classifier_seen = False
        for name, body in sorted(legs.items()):
            family = _family_of(name)
            if family is None:
                continue  # not harness-classified, or the classifier is absent
            classifier_seen = True
            declared_exec = _effective_execution(body)

            # ORDER MATTERS HERE, and the self-test is what established it.
            # An earlier draft skipped every non-live leg BEFORE comparing
            # `execution`, which silently exempted the denominator-INFLATING
            # direction: a row marked `live` for a leg config declares `shadow`
            # kept its cells inside the headline, and the matrix->config check
            # above cannot see it either because the NAME resolves fine. So the
            # agreement check runs for every declared leg that has a row, live
            # or not; only the missing-row check is scoped to live legs.
            rows_for_leg = rows_by_leg.get(name)
            if rows_for_leg:
                row_exec = rows_for_leg[0].get("execution")
                if row_exec != declared_exec:
                    problems.append(
                        f"{name}: matrix says execution='{row_exec}' but "
                        f"config/strategies.yaml resolves to '{declared_exec}' — the "
                        "denominator and the runtime disagree about this leg")
            elif _is_live(body):
                problems.append(
                    f"{name} [live, family={family}]: NO matrix row — the leg is "
                    "absent from the M20 denominator, so the coverage headline "
                    "is computed over an under-counted population")

        if not classifier_seen:
            problems.append(
                "m20_fleet_exit_sweep.classify could not be imported — NO leg "
                "was family-classified, so the config->matrix direction was "
                "NOT checked (this is 'unchecked', not 'clean')")

    for row, col, status in cells(matrix, live_only=False):
        who = f"{row.get('strategy')}/{row.get('symbol')}/{row.get('tf')}"
        cell = row.get(col)
        if cell is None:
            problems.append(f"{who}: column '{col}' absent")
            continue
        if not isinstance(cell, dict):
            problems.append(f"{who}: column '{col}' is not an object")
            continue
        if status is None:
            problems.append(
                f"{who} [{row.get('execution')}]: '{col}' status is null — "
                "not a legend value; a verdict was never recorded")
            continue
        if base(status) not in legend:
            problems.append(f"{who}: '{col}' status '{status}' not in legend")
            continue
        # Evidence rule (matrix _doc): a verdict comes from verified evidence,
        # never inference. Enforced on live rows, where the roll-up counts it.
        if row.get("execution") == "live" and base(status) != "pending":
            if not (cell.get("ref") or "").strip():
                problems.append(
                    f"{who}: '{col}' is '{status}' with no evidence ref")

        # A BARE `blocked` IS A COLLAPSED STATE (2026-08-13).
        #
        # `base()` maps `blocked:<reason>` -> `blocked`, which is right for the
        # headline and wrong as a place to STOP: once normalised, a cell that
        # says "we know exactly why — the OOS base is 4 against a floor of 25"
        # is indistinguishable from one where nobody ever established a cause.
        # Both print as `blocked` in the per-reason breakdown, and the second
        # kind is the one that becomes permanent, because a reason nobody wrote
        # down is a reason nobody revisits (the module docstring already warns
        # that `blocked:data_missing` rows rot; an UNLABELLED block rots faster).
        #
        # This is the matrix instance of CLAUDE.md's "collapsed states" rule:
        # `we did not look` and `we looked and it was too thin` must not share a
        # spelling. Found by audit, not by this guard — `mes_trend_long_1d`
        # `vol_trail` carried a bare `blocked` while its ref named the cause in
        # full, so the STATUS was silent about something the cell knew. One
        # instance; this check is what makes it a class.
        #
        # Deliberately NOT enforced against a fixed vocabulary of reasons: a new
        # blocking cause is a legitimate discovery (three of the six in use were
        # coined this week), and a closed list would push the next one toward
        # whichever existing label fits worst — which is how a taxonomy starts
        # lying. The requirement is only that a reason be STATED.
        if row.get("execution") == "live" and status == "blocked":
            problems.append(
                f"{who}: '{col}' is a bare 'blocked' with no ':<reason>' — "
                "state why it is blocked (e.g. blocked:insufficient_base). "
                "'blocked' alone cannot distinguish a measured cause from an "
                "unestablished one, and the roll-up's per-reason breakdown "
                "silently merges the two")
    return problems


def rollup(matrix: dict[str, Any]) -> dict[str, Any]:
    per_status: Counter[str] = Counter()
    per_lever: dict[str, Counter[str]] = defaultdict(Counter)
    open_cells: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    live_legs = {
        (r["strategy"], r["symbol"], r["tf"])
        for r in matrix["rows"] if r.get("execution") == "live"
    }

    for row, col, status in cells(matrix):
        b = base(status) or "MISSING"
        per_status[b] += 1
        per_lever[col][b] += 1
        if b in OPEN_STATUSES:
            open_cells[b].append(
                (row["strategy"], row["symbol"], row["tf"], col))

    total = sum(per_status.values())
    counts = {
        "resolved": sum(per_status[s] for s in RESOLVED),
        "validated": sum(per_status[s] for s in VALIDATED),
        "headline": sum(per_status[s] for s in HEADLINE),
    }
    return {
        "live_legs": len(live_legs),
        "lever_columns": len(matrix["lever_columns"]),
        "total_cells": total,
        "per_status": dict(per_status),
        "per_lever": {k: dict(v) for k, v in per_lever.items()},
        "counts": counts,
        "headline_pct": round(100 * counts["headline"] / total, 1) if total else 0.0,
        "cells_to_done": per_status["pending"] + per_status["blocked"],
        "open_cells": {k: sorted(v) for k, v in open_cells.items()},
        "matrix_updated_at": matrix.get("updated_at"),
        "evidence_vintage": evidence_vintage(matrix),
        "geometry_coverage": geometry_coverage(matrix),
    }


def geometry_coverage(matrix: dict[str, Any]) -> dict[str, Any]:
    """How much of the population states WHICH TP geometry produced its verdict.

    THE FRACTION IS THE POINT, not the marked cells. `tp_geometry` exists for
    exactly one job: BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP
    established that production places `tp = min(entry*1.099, entry + tp_r*risk)`
    while the harnesses modelled NO take-profit, so **every pre-2026-08-10
    verdict was measured on a book production does not run**. The field says
    which geometry a verdict rests on.

    Measured 2026-08-14: **10 of 416 cells carry it (2.4%)**. At that rate the
    absent value covers three different conditions at once — measured at live
    parity and unstamped, measured pre-cutover, and nobody looked — and the
    reassuring reading is the wrong one for most of the population.

    So this reports it the way `/performance` reports `rCoverage` and
    `pnlCoverage`: **the denominator ships beside the number**, and `unrecorded`
    is COUNTED rather than omitted. A reader must not be able to infer
    completeness from the marked cells alone, which is precisely what a bare
    list of stamped cells invites.

    Live cells only, matching the headline's population — a figure over a
    different denominator than the headline is how the 304/311/319 divergence
    started.

    Tracked by BL-20260814-TP-GEOMETRY-RECORDED-ON-2-PERCENT-OF-CELLS-SO-ABSENCE-CANNOT-MEAN-ANYTHING
    """
    counts: Counter[str] = Counter()
    for row, col, status in cells(matrix):
        if status is None:
            continue
        cell = row.get(col)
        geom = cell.get("tp_geometry") if isinstance(cell, dict) else None
        counts["unrecorded" if geom is None else str(geom)] += 1
    total = sum(counts.values())
    recorded = total - counts.get("unrecorded", 0)
    return {
        "total_cells": total,
        "recorded": recorded,
        "unrecorded": counts.get("unrecorded", 0),
        "recorded_pct": round(100 * recorded / total, 1) if total else 0.0,
        "by_value": dict(counts),
    }


def render(r: dict[str, Any]) -> str:
    t = r["total_cells"]
    out = [
        "M20 exit-refinement coverage roll-up",
        "=" * 60,
        f"population: {r['live_legs']} LIVE legs x {r['lever_columns']} levers "
        f"= {t} cells   (matrix updated_at {r['matrix_updated_at']})",
        "",
        "  HEADLINE (progress; counts `blocked` as closed — the figure to quote)",
        f"    {r['counts']['headline']}/{t} = {r['headline_pct']}%",
        "",
        "  narrower cuts, for reference — quoting one of these as 'coverage'",
        "  is what produced the 304/311/319 divergence:",
        f"    validated only (excl. blocked) : {r['counts']['validated']}/{t}"
        f" = {round(100 * r['counts']['validated'] / t, 1)}%",
        f"    resolved only  (excl. also     : {r['counts']['resolved']}/{t}"
        f" = {round(100 * r['counts']['resolved'] / t, 1)}%",
        "                    passed_unshipped, shipped_gate_failed)",
        "",
        "  DONE-CONDITION (skill: no pending AND no blocked on live legs)",
        f"    {r['cells_to_done']} cells remain "
        f"({r['per_status'].get('pending', 0)} pending "
        f"+ {r['per_status'].get('blocked', 0)} blocked)",
        "    ^ NOT 376 - headline. `blocked` is closed for the headline and",
        "      open for the done-condition, deliberately.",
        "",
    ]
    g = r.get("geometry_coverage") or {}
    if g.get("total_cells"):
        # Reported like rCoverage/pnlCoverage: the DENOMINATOR ships with the
        # number, and `unrecorded` is counted rather than omitted. Without this
        # a reader infers completeness from the stamped cells alone -- and at
        # 2.4% stamped that inference is wrong for almost the whole population.
        out += [
            "  TP-GEOMETRY COVERAGE (which geometry each verdict rests on)",
            f"    recorded: {g['recorded']}/{g['total_cells']}"
            f" = {g['recorded_pct']}%   unrecorded: {g['unrecorded']}",
            "    " + " · ".join(f"{k}={v}" for k, v in
                                sorted(g.get("by_value", {}).items())),
            "    ^ `unrecorded` is NOT 'measured at live parity'. It covers",
            "      three conditions at once: stamped-nowhere-but-live-parity,",
            "      pre-2026-08-10 no-take-profit, and nobody looked. Every",
            "      verdict older than the 2026-08-10 cutover was measured on a",
            "      book production does not run.",
            "",
        ]
    out += ["status counts:"]
    v = r.get("evidence_vintage") or {}
    if not v.get("classifier_available", True):
        out[2:2] = [
            "",
            "  ⚠️  EVIDENCE VINTAGE: NOT COMPUTED — the family classifier could",
            "      not be imported. This is not 'no staleness found'.",
        ]
    elif v.get("pre_cutover") or v.get("undated"):
        graded = v["pre_cutover"] + v["post_cutover"] + v["undated"]
        pct = round(100 * v["pre_cutover"] / graded, 1) if graded else 0.0
        block = [
            "",
            f"  ⚠️  EVIDENCE VINTAGE — {v['pre_cutover']} of {graded} closed cells"
            f" ({pct}%) on the {v['affected_legs']} legs whose harness modelled",
            f"      NO take-profit were measured BEFORE their harness's TP fix"
            f" ({v['undated']} more carry no date at all).",
            f"      Cutover is PER-LEVER, not one date: default {v['cutover']}"
            f" (the lever-sweep harness), and"
            + (" " + ", ".join(f"{k} {d}" for k, d in
                               sorted((v.get('cutovers') or {}).items()))
               if v.get("cutovers") else " no overrides")
            + " (its own driver).",
            "      BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP: those"
            " live units clamp the TP to 9.9%",
            "      from entry (~1.3-5R at real ATR), so the lever was tuned"
            " against a book production does not run.",
            f"      Families: {', '.join(v['affected_families'])}."
            f" The other {v['clean_legs']} legs (scalp, fvg) place a real target"
            " their harness models.",
            "      The headline above is NOT wrong — it counts cells that were"
            " genuinely processed. It is",
            "      conditioned on that geometry, and until 2026-08-12 nothing"
            " anywhere said so.",
        ]
        # Appended to the SAME list rather than spliced at a fixed index — the
        # first attempt hardcoded `out[12:12]` and landed after the population
        # line, separating the ⛔ block from the caveat it qualifies.
        dec = v.get("stale_decisions") or []
        if dec:
            by_status: dict[str, int] = {}
            for _leg, _lev, status, _dt, _cut in dec:
                k = base(status) or "?"
                by_status[k] = by_status.get(k, 0) + 1
            block += [
                "",
                f"      ⛔ {len(dec)} of those stale cells are NOT negatives —"
                " they are live DECISIONS:",
                "         " + " · ".join(
                    f"{s_} {n_}" for s_, n_ in
                    sorted(by_status.items(), key=lambda kv: -kv[1])),
                "         A stale NEGATIVE costs knowledge (the lever might have"
                " passed and we would not know).",
                "         A stale SHIPPED costs MONEY — it changes exit behaviour"
                " on a real-money leg now, on a",
                "         number never reproduced under the geometry the bot"
                " actually places.",
                "         Read the re-sweep base rate from the CORPUS, not from"
                " this banner: a rate",
                "         hardcoded in printed text goes stale silently, and"
                " this one did.",
                "         List them with `--stale-decisions`.",
            ]
        out[2:2] = block
    for s, n in sorted(r["per_status"].items(), key=lambda kv: -kv[1]):
        out.append(f"    {s:<22} {n:>4}")
    out += ["", "per-lever open cells (pending + blocked):"]
    for lever, counts in r["per_lever"].items():
        opened = sum(counts.get(s, 0) for s in OPEN_STATUSES)
        if opened:
            detail = " ".join(
                f"{s}={counts[s]}" for s in OPEN_STATUSES if counts.get(s))
            out.append(f"    {lever:<20} {opened:>3}   ({detail})")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", default=str(MATRIX))
    ap.add_argument("--json", action="store_true", help="emit the roll-up as JSON")
    ap.add_argument("--validate", action="store_true",
                    help="structural checks only; non-zero exit on a defect")
    ap.add_argument("--check", action="store_true",
                    help="CI mode: validate, and fail on any defect")
    ap.add_argument("--done-condition", action="store_true",
                    help="list every cell blocking the milestone (pending + blocked)")
    ap.add_argument("--stale-decisions", action="store_true",
                    help="list the CLOSED cells that are not negatives and whose "
                         "evidence predates the TP-parity cutover — live decisions "
                         "resting on a number never reproduced under live geometry")
    ap.add_argument("--stale-corpus-state", action="store_true",
                    help="for every stale cell, say whether the CORPUS already "
                         "answers it: no live-parity row / agrees / DISAGREES. "
                         "'Stale' alone sends a reader to re-run a sweep that may "
                         "already have been run (measured 2026-08-14: 100 of 186 "
                         "stale cells already had a live-parity row)")
    a = ap.parse_args(argv[1:])

    path = Path(a.matrix)
    if not path.exists():
        print(f"matrix not found: {path}", file=sys.stderr)
        return 2
    matrix = load(path)

    problems = validate(matrix)
    if a.validate or a.check:
        if problems:
            print(f"coverage-matrix validation: {len(problems)} DEFECT(S)")
            for p in problems:
                print(f"  - {p}")
            return 1
        print("coverage-matrix validation: OK "
              "(all statuses in legend, all closed live cells carry a ref)")
        return 0

    r = rollup(matrix)
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(render(r))
        if a.done_condition:
            print("\ncells blocking the done-condition:")
            for bucket in OPEN_STATUSES:
                rows = r["open_cells"].get(bucket) or []
                print(f"\n  {bucket} ({len(rows)}):")
                for strategy, symbol, tf, col in rows:
                    print(f"    {strategy:<26} {symbol:<9} {tf:<4} {col}")
        if a.stale_decisions:
            v = r["evidence_vintage"]
            dec = v.get("stale_decisions") or []
            # STATE THE DENOMINATOR THIS RANGES OVER. `stale_decisions` is a
            # subset of the stale population, so an empty list means one of two
            # opposite things: every stale cell is a negative (good), or the
            # stale population is itself empty / never computed (nothing was
            # examined). Printing "none" alone collapses them — the sub-class C
            # shape this repo guards for, caught here by
            # diagnostic-provenance-guard on my own diff.
            stale_n = v.get("pre_cutover", 0) + v.get("undated", 0)
            print(f"\nstale DECISIONS ({len(dec)} of {stale_n} stale cells) — "
                  f"closed, not negative, evidence older than the cutover for "
                  f"THAT lever (printed per row; default {v['cutover']}):")
            if not v.get("classifier_available", True):
                print("  NOT COMPUTED — the family classifier could not be "
                      "imported. This is not 'no stale decisions found'.")
            elif stale_n == 0:
                print("  (0 stale cells in the population — nothing to grade, "
                      "which is NOT the same as 'all clear')")
            elif not dec:
                print(f"  (0 of {stale_n} stale cells is a non-negative — every "
                      "one is an honest_negative)")
            for leg, lever, status, dt, cut in sorted(dec):
                print(f"    {leg:<26} {lever:<16} {str(status):<22} "
                      f"newest-ref {dt or '(undated)'}  (cutover {cut})")
        if a.stale_corpus_state:
            cs = stale_corpus_state(matrix)
            if not cs["available"]:
                # "We could not look" is NOT "there is nothing to find". Saying
                # so is the whole point of the third state.
                print(f"\nstale CORPUS STATE: NOT COMPUTED ({cs.get('why')}). "
                      f"This is not 'no newer evidence exists'.")
            else:
                c = cs["counts"]
                total = sum(c.values())
                print(f"\nstale cells vs the CORPUS ({total} stale cell(s)) — does "
                      f"newer live-parity evidence already exist?")
                print(f"  {CORPUS_NO_ROW:<26} {c.get(CORPUS_NO_ROW, 0):>4}  "
                      f"— nothing newer; a re-run IS the remedy")
                print(f"  {CORPUS_AGREES:<26} {c.get(CORPUS_AGREES, 0):>4}  "
                      f"— newer evidence exists and matches the status")
                print(f"  {CORPUS_DISAGREES:<26} {c.get(CORPUS_DISAGREES, 0):>4}  "
                      f"— newer evidence exists and CONTRADICTS the status")
                dis = [r for r in cs["rows"] if r["state"] == CORPUS_DISAGREES]
                if dis:
                    print("\n  the contradictions (adjudicate; do NOT auto-flip — "
                          "a passing cell is not a passing lever disposition, and "
                          "a live-leg status change is Tier-3):")
                    for r in sorted(dis, key=lambda x: (x["leg"], x["lever"])):
                        print(f"    {r['leg']:<26} {r['lever']:<16} "
                              f"{str(r['status']):<18} vs {r['corpus_cell']} "
                              f"{r['corpus_verdict']} {r['corpus_run']} "
                              f"base_OOS={r['corpus_base_oos']}")
        if problems:
            print(f"\n⚠️  {len(problems)} structural defect(s) — run --validate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
