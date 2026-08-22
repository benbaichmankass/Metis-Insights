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
# A DATE CANNOT SAY "NEVER", AND ONE LEVER NEEDS IT TO (2026-08-14).
#
# This map's contract is "the date THIS lever's harness started modelling the
# live TP". For `regime_flip_exit` that harness — `m20_flip_replay_sweep.py` —
# HAS NOT STARTED: it still calls `base_args(name, cfg, fam, data, resample)`
# with no `tp_cap_pct`, which defaults to 0.0, and `base_args` then appends
# neither `--tp-cap-pct` NOR `--tp-r` for a capped family. Demonstrated on
# `trend_donchian_eth_4h` (family `donchian`): the positional call yields both
# flags absent, the explicit call yields both present.
#
# Falling back to the default date was therefore an ANSWER SHAPED LIKE A RIGHT
# ONE for the second time in this file: it asserts that column's evidence became
# live-parity on 2026-08-10, so any cell dated after that reads `post_cutover`.
# Measured against the committed matrix, 42 of the 43 `regime_flip_exit`
# negatives are capped-family legs (donchian 22, pullback 19, squeeze 1) — i.e.
# essentially the whole negative column was graded on a book with no take-profit
# and was scoring as clean on a date test.
#
# `NEVER` is the honest value: no cell in such a column can be post-cutover,
# whatever its date, until the harness is fixed. Removing the entry is what
# marks the fix — not editing a date.
GEOMETRY_CUTOVER_NEVER = "NEVER"

LEVER_GEOMETRY_CUTOVER = {
    # the day `m20_exit_head_round.py` gained `--tp-cap-pct` (live parity as the
    # DEFAULT) and began stamping `_round_meta.tp_geometry` into round_report.json
    "exit_head_ml": "2026-08-14",
    # `regime_flip_exit` HELD THIS SENTINEL AND NO LONGER DOES (2026-08-16).
    #
    # The entry read: "`m20_flip_replay_sweep.py` still calls base_args
    # positionally — its books model NO take-profit for every
    # donchian/pullback/fade/squeeze leg, and it stamps no geometry at all, so
    # there is not even a field to check."
    # (BL-20260814-THREE-SIBLING-SWEEPS-STILL-BUILD-NO-TAKE-PROFIT-BOOKS-AND-STAMP-NOTHING)
    #
    # Both halves are now false, and REMOVING THE ENTRY IS HOW THAT IS MARKED —
    # the comment above says so explicitly, and it is deliberately not an edit
    # to a date. Two conditions, both met and both verifiable:
    #   1. the harness passes the cap (`base_args(..., a.tp_cap_pct)`, default
    #      0.099) and stamps `tp_geometry` per leg and run-level; and
    #   2. a real sweep LANDED on that harness — 42 legs at
    #      `tp_geometry: live_parity_capped`, 30 fail / 12
    #      INERT_NEVER_FLIPPED / 0 PASS (relay #9536, read #9540/#9541) — and
    #      its 41 matching matrix cells now carry both the measurement in
    #      their `ref` and an explicit `tp_geometry: live_parity`.
    # Condition 2 is why this was NOT removed in the same commit as the
    # harness fix: a fixed harness with no evidence behind it still leaves
    # every cell measured on the old book.
}


def cutover_for(lever: str) -> str:
    """The date THIS lever's harness started modelling the live TP.

    May return `GEOMETRY_CUTOVER_NEVER` — the harness has not been fixed, so no
    date can clear a cell in that column. Callers must handle that explicitly
    rather than comparing it as a date: `max(dates) < "NEVER"` is True for every
    ISO date by string order, which would accidentally give the right answer for
    the wrong reason and break silently the day the sentinel changed.
    """
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
    # `bracket_geometry` (2026-08-20) is not a ninth lever but it belongs here
    # for the strongest possible reason: it SWEEPS the take-profit, so a cell
    # measured before the harness could place the live capped TP is not merely
    # aged, it graded a different bracket than the one that trades. Its own
    # cells were measured net-of-fees WITH the cap applied, so none is stale
    # today — the entry states the RULE, not a current condition.
    "bracket_geometry",
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


def _funding_by_leg() -> dict[str, str] | None:
    """LEG NAME -> funding class of the accounts that declare it, or None.

    ⚠️ RENAMED from `_funding_by_symbol` 2026-08-15, and the rename IS the fix's
    second half. The first version keyed on symbols; after re-keying on each
    account's `strategies` list the old name described a mapping the function no
    longer returned — the exact "label does not describe what it computes" class
    this module exists to police, left inside the correction for it.

    THREE STATES, NEVER COLLAPSED — `real_money` / `paper` / `unresolved`.
    Added 2026-08-15 because the ⛔ stale-DECISIONS banner asserted a stale
    `shipped` cell "changes exit behaviour on a real-money leg now" for EVERY
    row, while nothing in this script had ever read `config/accounts.yaml`.
    Measured against the field the same day, that label was wrong for 3 of the
    4 rows it was printed over: `mes_trend_long_1d` and `mhg_pullback_1d` route
    to `ib_paper` (paper) and `ib_live` (real_money but `mode: dry_run`), while
    only `htf_pullback_trend_2h` (BTCUSDT -> `bybit_2`) is money-at-risk. That
    is CLAUDE.md § "Diagnostic provenance" sub-class **A** — the label names a
    quantity the code never computed — in the very tool written to stop that
    class, which is why it is fixed here rather than reworded.

    `real_money` requires **both** gates the runtime requires: an account whose
    `account_class` is `real_money` AND whose `mode` is `live`. `ib_live` is
    `real_money` at `mode: dry_run`, so it places no live order and must not
    make a leg read as money-at-risk.

    None on an unreadable config — the same third state as `_declared_legs`.
    The caller renders that as `unresolved`, which is emphatically NOT `paper`:
    "we could not look" and "we looked and it is paper" are opposite claims,
    and defaulting the unknown to the safe-sounding one is the exact shape
    § "Collapsed states" exists to forbid.

    Reads through the CANONICAL `src.config.accounts_loader.load_accounts_dict`,
    not a hand-rolled `yaml.safe_load` — `canonical-config-loaders` caught my
    first version doing the latter, correctly: eight hand-rolled parsers of this
    file once existed and one of them iterated the wrong shape and silently
    returned nothing on every live run.

    ⚠️ The canonical loader returns `{}` on a read/parse failure, which COLLAPSES
    "could not read" into "no accounts" — the very distinction this function
    exists to preserve. It takes an `errors` list for exactly that reason, so a
    captured error maps back to `None` here rather than to an empty mapping that
    would render every leg `unresolved` *without saying why*.
    """
    sys.path.insert(0, str(REPO))
    try:
        from src.config.accounts_loader import load_accounts_dict
    except ImportError:
        return None
    errors: list[dict[str, Any]] = []
    accounts = load_accounts_dict(REPO / "config" / "accounts.yaml", errors=errors)
    if errors or not isinstance(accounts, dict) or not accounts:
        return None
    # KEYED ON THE ACCOUNT'S `strategies` LIST, NOT ON `symbols`.
    #
    # The first version of this keyed on symbols and was UNSOUND, caught
    # 2026-08-15 while prioritising the stale population: `eth_pullback_prop_2h`
    # and `eth_pullback_2h` both trade ETHUSDT, so a symbol map graded BOTH
    # `real_money` -- but only the latter is declared by `bybit_2`; the prop leg
    # is declared solely by `breakout_1`. A symbol map answers "does some live
    # real-money account trade this instrument", which is NOT the question. Every
    # account declares `strategies` explicitly, so the leg->account edge is
    # available and exact; there is no reason to infer it.
    #
    # FOUR states, because `account_class` has THREE values in the field --
    # measured, not assumed: paper x7, real_money x3, **prop x1**
    # (`breakout_1`, mode live, a $5k funded account). Prop is a distinct
    # funding class this repo never blends into real-money or paper KPIs, and
    # folding it into either would be the same collapse this module exists to
    # stop. Precedence real_money > prop > paper: a leg routed to both reads as
    # the strongest money-at-risk claim.
    out: dict[str, str] = {}
    rank = {"real_money": 3, "prop": 2, "paper": 1}
    for body in accounts.values():
        if not isinstance(body, dict):
            continue
        cls = body.get("account_class")
        live = body.get("mode") == "live"
        # A dry_run account places no order, so it cannot make a leg
        # money-at-risk whatever its class (`ib_live` is real_money at dry_run).
        grade = cls if (live and cls in ("real_money", "prop")) else "paper"
        for leg in (body.get("strategies") or []):
            if rank[grade] > rank.get(out.get(leg, "paper"), 0) or leg not in out:
                out[leg] = grade if rank[grade] > rank.get(out.get(leg), 0) else out[leg]
    return out


def _leg_funding(leg: str | None, routing: dict[str, str] | None) -> str:
    """`real_money` / `prop` / `paper` / `unresolved` for one leg NAME.

    `unresolved` means the config was unreadable OR no account declares this
    leg -- deliberately NOT folded into `paper`. "We could not look" and "we
    looked and it is paper" are opposite claims.
    """
    if routing is None or not leg:
        return "unresolved"
    return routing.get(leg, "unresolved")


def _stale_decision_funding(dec: list) -> dict[str, int]:
    """Counts of `real_money` / `paper` / `unresolved` over stale decisions.

    Resolved at PRINT time rather than folded into the `stale_decisions` tuple,
    so the vintage computation stays a pure function of the matrix and the
    tuple shape stays stable for its existing consumers in
    `tests/test_exit_head_per_leg.py`.
    """
    by_leg = _funding_by_leg()
    out: dict[str, int] = {}
    for leg, *_rest in dec:
        k = _leg_funding(leg, by_leg)
        out[k] = out.get(k, 0) + 1
    return out


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
            # WHY a cell is stale, not just THAT it is. Four different
            # conditions set `stale` below and only ONE of them is "the
            # evidence predates the cutover" — yet that was the only reason the
            # printed header stated. Surfaced 2026-08-15 by the first row where
            # the two diverge: `htf_pullback_trend_2h`/`trail_geometry` gained a
            # 2026-08-15 live-parity re-sweep and so prints `newest-ref
            # 2026-08-15` under a cutover of 2026-08-10 — a row dated AFTER the
            # bar, sitting in a list whose header says everything in it is
            # older. It is held stale by its declared `no_take_profit` geometry,
            # which the date cannot overrule, and a reader had no way to see
            # that from the output. Sub-class A: the label named a quantity the
            # code did not compute.
            why = None
            if geom == GEOMETRY_NO_TP:
                stale = True          # measured; the date cannot overrule it
                why = "geometry=no_take_profit (declared; date cannot clear it)"
            elif geom == GEOMETRY_LIVE_PARITY:
                stale = False         # declared by the round itself
            elif cut == GEOMETRY_CUTOVER_NEVER:
                # The harness never started modelling the live TP, so the date
                # test cannot clear this cell. Counted as undeclared too: we
                # still did not look at THIS cell's geometry, we merely know its
                # producer could not have got it right.
                out["geometry_undeclared"] += 1
                stale = True
                why = "harness never modelled the live TP (cutover=never)"
            else:
                out["geometry_undeclared"] += 1
                stale = (not dates) or max(dates) < cut
                if stale:
                    why = ("undated" if not dates
                           else f"evidence {max(dates)} predates cutover {cut}")
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
                     max(dates) if dates else None, cut, why))
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
# …AND THE ONE WHERE A RE-RUN IS NOT THE REMEDY (2026-08-16).
#
# `CORPUS_NO_ROW`'s comment prescribes an action, which is the useful thing
# about it and also the thing that made it wrong for a third of its rows. A
# lever pinned at `GEOMETRY_CUTOVER_NEVER` has a harness that does not model
# the live TP, so a re-run of it produces ANOTHER stale row: the remedy is to
# fix the harness, and only then sweep. Measured on the committed matrix the
# day this was added: of 99 `no_live_parity_row` cells, **38 were
# `regime_flip_exit`** — every one labelled with the expensive action instead
# of the correct one, and it is the largest single block in the backlog.
#
# This is self-clearing by construction: the state is derived from
# `cutover_for(lever)`, so deleting a lever's `LEVER_GEOMETRY_CUTOVER` entry
# (which is how a harness fix is marked) returns its cells to `CORPUS_NO_ROW`
# with no edit here. A hardcoded lever list would have to be remembered.
CORPUS_HARNESS_UNFIXED = "harness_never_modelled_the_tp"
# …AND THE ONE WHERE THERE IS NOTHING TO RE-RUN AT ALL (2026-08-16).
#
# The second time the same prescription was wrong, found by breaking the
# remainder down instead of quoting it. `exit_ladder` is 29 of the 61 cells
# left after the NEVER split, and **no backtest driver produces that column**:
# `m20_fleet_exit_sweep.cells_for` emits cells for 5 of the 8 lever columns,
# and the three it does not are `exit_head_ml` (own driver,
# `m20_exit_head_round`), `regime_flip_exit` (own driver,
# `m20_flip_replay_sweep`) and `exit_ladder` (**none**). Its cells cite the
# 2026-07-12 memo's 20-cell banking study; `m20_exit_analysis.py` reads the
# LIVE `exit_ladder_soak.jsonl`, which is an observer, not a producer of matrix
# cells.
#
# So "a re-run IS the remedy" was pointing at a sweep that cannot emit the
# column. Building a producer (or re-doing the study) is the remedy, and that
# is a different kind of work with a different cost — which is the whole reason
# to say it rather than let someone discover it at the console.
COLUMNS_WITH_A_SWEEP_PRODUCER = frozenset({
    "stale_stop", "giveback_stop", "trail_decay", "vol_trail",
    "trail_geometry",
})
# Declared rather than introspected, because regexing `cells_for`'s source for
# lever literals is a probe adjacent to the question. Kept honest by a test
# that CALLS `cells_for` over real configs and compares what it actually emits
# against this set, so the two cannot drift silently.
CORPUS_NO_PRODUCER = "no_sweep_driver_emits_this_column"
# Columns the FLEET sweep does not emit but which have a producer of their own,
# so "no driver" would be false for them. Named separately from the set above
# because the two facts are different: one is "the fleet sweep covers it", the
# other is "something covers it".
_COLUMNS_WITH_THEIR_OWN_DRIVER = frozenset({
    "exit_head_ml",       # scripts/research/m20_exit_head_round.py
    "regime_flip_exit",   # scripts/research/m20_flip_replay_sweep.py
    "bracket_geometry",   # scripts/research/e35_bracket_geometry_sweep.py
})


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
            # Checked BEFORE `CORPUS_NO_ROW`, because both are "nothing newer
            # exists" and only one of them can be answered by sweeping.
            # Order matters and is not arbitrary: a BROKEN producer is a more
            # specific finding than a MISSING one, and `regime_flip_exit` has
            # a producer (its own driver), so it must not fall through to
            # "nothing emits this column".
            if cutover_for(lever) == GEOMETRY_CUTOVER_NEVER:
                state = CORPUS_HARNESS_UNFIXED
            elif lever not in COLUMNS_WITH_A_SWEEP_PRODUCER \
                    and lever not in _COLUMNS_WITH_THEIR_OWN_DRIVER:
                state = CORPUS_NO_PRODUCER
            else:
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


# One name, one place. Referenced in an error message a human acts on, so it
# must resolve — and a backlog id that only ever appears as a wrapped string
# literal is exactly the fragment artifact-validity-guard exists to reject.
_GEOMETRY_BACKLOG_ROW = (
    "BL-20260814-TP-GEOMETRY-RECORDED-ON-2-PERCENT-OF-CELLS-SO-ABSENCE-CANNOT-MEAN-ANYTHING"
)


def tp_geometry_legend_values(matrix: dict[str, Any]) -> set[str]:
    """The DEFINED `tp_geometry` values, read from the matrix's own legend.

    Read, never hardcoded. A guard carrying its own copy of the vocabulary is
    free to drift from the legend a human reads, and then the two disagree
    about what a cell means while both look authoritative. Keys starting with
    `_` are the legend's prose (`_field`, `_why_it_exists`, `ABSENT_means_…`),
    not values.
    """
    leg = matrix.get("tp_geometry_legend") or {}
    return {k for k in leg if not k.startswith("_") and k != "ABSENT_means_unrecorded"}


def _validate_tp_geometry(matrix: dict[str, Any]) -> list[str]:
    """Criterion (3) of BL-20260814-TP-GEOMETRY-RECORDED-ON-2-PERCENT-OF-CELLS-SO-ABSENCE-CANNOT-MEAN-ANYTHING.

    TWO CHECKS, and the split is the whole design.

    (a) **Every PRESENT value must be a legend value.** Cheap, always-on, and
        it catches the failure that `status: null` already demonstrated on this
        same file: a value that is not in the legend cannot be graded by
        anything, yet wears a graded value's shape.

    (b) **The unstamped count may not GROW.** This is what makes "a new cell
        cannot be added silent" enforceable TODAY. 210 of 376 live cells carry
        no `tp_geometry`, so an "every cell must carry one" assertion would
        fail on the current file and could only ship by being switched off —
        a guard nobody can turn on is worth less than no guard. A ratchet
        grandfathers the existing absences (which
        `BL-20260814-…-SO-ABSENCE-CANNOT-MEAN-ANYTHING` owns, and which must NOT
        be guessed from dates) while making any NEW silent cell a CI failure.

    THE CEILING LIVES IN THE MATRIX, next to the legend it bounds, so a PR that
    stamps cells lowers it deliberately and a PR that adds a silent cell has to
    raise it in the diff where a reviewer sees it. A ceiling stored in this
    script would be a number nobody reads next to the data it describes.

    ⚠️ THE CEILING IS NOT A TARGET. It is an upper bound on a known-bad
    population, and the row that owns stamping is still open. Do not read a
    passing ratchet as "geometry coverage is fine" — read
    `geometry_coverage()`'s fraction for that, which is the honest number.
    """
    problems: list[str] = []
    defined = tp_geometry_legend_values(matrix)
    if not defined:
        # NOT silently skipped. An unreadable legend means we could not check,
        # which is a different statement from "every value is valid" — the
        # collapsed-state shape this file guards for elsewhere.
        return ["tp_geometry_legend is missing or defines no values — "
                "tp_geometry NOT validated (this is 'unchecked', not 'clean')"]

    unstamped = 0
    for row, col, _status in cells(matrix, live_only=True):
        cell = row.get(col)
        if not isinstance(cell, dict):
            continue
        if "tp_geometry" not in cell or cell.get("tp_geometry") is None:
            unstamped += 1
            continue
        geom = cell.get("tp_geometry")
        if geom not in defined:
            problems.append(
                f"{row.get('strategy')}/{row.get('symbol')}/{row.get('tf')}/{col}: "
                f"tp_geometry={geom!r} is not a defined value {sorted(defined)} — "
                "nothing can grade which geometry this verdict rests on")

    leg = matrix.get("tp_geometry_legend") or {}
    ceiling = leg.get("_unstamped_ceiling")
    if not isinstance(ceiling, int):
        problems.append(
            "tp_geometry_legend._unstamped_ceiling is missing or not an int — "
            "the ratchet that stops a new silent cell cannot be evaluated "
            f"(currently {unstamped} live cells carry no tp_geometry)")
    elif unstamped > ceiling:
        problems.append(
            f"{unstamped} live cells carry no tp_geometry, above the recorded "
            f"ceiling of {ceiling}. A cell was added or un-stamped without "
            "recording which TP geometry its verdict rests on. Either stamp it "
            "(live_parity / no_take_profit — NEVER guessed from the date, see "
            # The id is kept WHOLE on one line on purpose: splitting it across a
            # string concat is how this very message first shipped, and
            # artifact-validity-guard correctly read the fragment as a reference
            # resolving to nothing.
            f"{_GEOMETRY_BACKLOG_ROW}) or raise the ceiling in the diff so a "
            "reviewer sees the population growing.")
    return problems


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

    problems += _validate_tp_geometry(matrix)

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


# The E1 fold block size. SINGLE-HOMED here as a named constant so the printed
# bound and the code that produces it cannot drift apart in prose; the value is
# `train_exit_head.py`'s `--min-fold-trades` default (50), derived in
# docs/research/M20-E1-block-size-derivation-2026-08-13.md and NOT a knob to
# tune for reachability (that doc forbids it: P_detect is not monotonic in b).
_E1_BLOCK = 50


def usable_folds(n_trades: int, block: int = _E1_BLOCK) -> int:
    """`u` for a leg with `n_trades` lifetime harness trades.

    Mirrors `train_exit_head.fold_blocks`: `range(block, len(ordered)-block+1,
    block)`. Deliberately the same arithmetic rather than a closed form, so a
    change to the fold construction shows up here as a diff rather than as a
    silently stale formula.
    """
    return len(range(block, n_trades - block + 1, block))


def fold_reachability(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    """Per blocked `exit_head_ml` cell: is the block arithmetic, or data?

    READS the `lifetime_trades` field. It exists because that number lived only
    in ref PROSE, so nothing could recompute the bound and two different
    arithmetics coexisted in one lever column for a day — three cells reasoned
    from the LEVER sweep's date split and named an "earlier split" route the
    fold code forecloses. A field that is written and never read is worse than
    a missing one, so this is the reader.

    A cell with the blocked status and NO `lifetime_trades` is REPORTED as
    ungraded rather than skipped — silently dropping it would report a
    denominator this never measured.
    """
    out: list[dict[str, Any]] = []
    for row in matrix.get("rows") or []:
        cell = row.get("exit_head_ml")
        if not isinstance(cell, dict):
            continue
        if not str(cell.get("status") or "").startswith("blocked:insufficient_lifetime"):
            continue
        n = cell.get("lifetime_trades")
        if not isinstance(n, int):
            out.append({"strategy": row.get("strategy"), "lifetime_trades": None,
                        "usable_folds": None, "short_by": None,
                        "ungraded_why": "cell carries no `lifetime_trades` field"})
            continue
        u = usable_folds(n)
        out.append({"strategy": row.get("strategy"), "lifetime_trades": n,
                    "usable_folds": u, "short_by": max(0, 3 * _E1_BLOCK - n)})
    out.sort(key=lambda x: (x["lifetime_trades"] is None, x["lifetime_trades"] or 0))
    return out


# The done-condition cells, grouped by WHAT ACTUALLY GATES THEM.
#
# WHY: the roll-up used to print `read the done-condition as {N} actionable +
# {M} arithmetic`, computed as `cells_to_done - len(unreachable)`. That subtracts
# exactly ONE gate -- the exit_head_ml fold arithmetic -- and silently calls
# every other gate "actionable". Measured 2026-08-17 it reported 12 actionable
# where about 4 were workable by a session, and because the line was EMITTED BY
# THIS SCRIPT the overstatement reached every consumer, a roadmap entry and three
# operator pings before anyone read the block reasons
# (BL-20260817-M20-ACTIONABLE-COUNT-OVERSTATES-WHAT-A-SESSION-CAN-DO).
#
# The partition keys on the STATUS STRING the matrix already carries, so it is
# reproducible and cannot drift from the cell. It deliberately does NOT try to
# recover "parked milestone-wide" or "measured, grading withheld" -- those live
# in ref PROSE, are real judgements, and a tool that guessed at them would be
# manufacturing a classification. A hand analysis may legitimately use them; the
# totals reconcile either way.
GATE_KINDS = {
    # the leg has not traded enough for the protocol's folds/floors
    "insufficient_lifetime_trades": "accrual",
    "insufficient_base": "accrual",
    "insufficient_oos_base_at_derived_split": "accrual",
    # the candles/history do not exist to sweep against
    "native-history-thin": "data",
    "data_missing": "data",
    # the harness cannot express the lever at all -- needs CODE, not a run
    "no_harness_levers": "harness_gap",
}

#: Levers a SESSION cannot produce a verdict for, whatever a cell's status says.
#:
#: WHY THIS EXISTS. `gate_partition` keys on the cell's STATUS STRING, which is
#: the right call (it cannot drift from the matrix) and is also blind to whether
#: the thing the status implies is POSSIBLE. A `pending` cell reads
#: "no sweep has been run", which invites "so run it" -- and for these two
#: levers no run exists to be run. Measured 2026-08-17: all FOUR cells the
#: partition called movable rest on these two levers, so the honest movable
#: count was ZERO while the roll-up implied four
#: (BL-20260817-ROLLUP-CALLS-A-CELL-MOVABLE-WHEN-NO-SWEEP-PATH-EXISTS).
#:
#: THE THREE FACTS BEHIND EACH ENTRY, each verified rather than inherited:
#:   * `m20_fleet_exit_sweep.py` defines NO arm -- each lever appears exactly
#:     once, in the comment saying it is absent.
#:   * neither `backtest_trend.py` nor `backtest_squeeze.py` exposes a flag
#:     (`--exit-ladder` / `--regime-flip`: none, AST-checked).
#:   * `scripts/ci/check_matrix_corpus_agreement.py` states the same, and is
#:     the authority the corpus-agreement guard already reads.
#:
#: NOT a claim the cells are unreachable forever -- both have a named route
#: (a backtest-gated P4 graduation; an operator decision). It is a claim that
#: the route is not "a session runs the sweep". `exit_head_ml` is deliberately
#: ABSENT: it has its own driver and IS swept, so its cells are graded
#: `arithmetic`/`accrual` on trade counts, which is a different gate.
LEVERS_WITHOUT_A_SWEEP_PATH: dict[str, str] = {
    "exit_ladder": (
        "observe-only shadow soak (runtime_logs/exit_ladder_soak.jsonl); the "
        "fleet harness never emits an exit_ladder cell, and graduation to a "
        "real laddered exit is the backtest-gated P4"
    ),
    "regime_flip_exit": (
        "no runtime implementation to sweep -- only the offline replays "
        "m20_regime_flip_replay.py / m20_flip_replay_sweep.py; building it as "
        "a YAML-declared default-off close path is operator decision (c)"
    ),
}


def gate_partition(matrix: dict[str, Any],
                   reach: list[dict[str, Any]]) -> dict[str, Any]:
    """Group the open cells by gate kind, reusing `reach` for the arithmetic cut.

    `reach` is passed in rather than recomputed so the arithmetic subset here and
    the reachability table below can never disagree about which legs are
    unreachable -- two derivations of one number drifting apart is the defect
    this file has now hit more than once.

    An `unclassified` bucket is always reported. A new `blocked:<reason>` must
    surface as unclassified rather than land in a neighbouring bucket, because a
    silent default would make the partition look complete while mis-stating it --
    the same collapse the guard family elsewhere in this repo exists to prevent.
    """
    arith_legs = {r["strategy"] for r in reach if r.get("usable_folds") == 0}
    buckets: dict[str, list[tuple[str, str, str, str, str]]] = defaultdict(list)
    for row, col, status in cells(matrix):
        if row.get("execution") != "live":
            continue
        st = status if isinstance(status, str) else ""
        if base(st) not in OPEN_STATUSES:
            continue
        ident = (row["strategy"], row["symbol"], row["tf"], col, st)
        if st == "pending":
            buckets["never_attempted"].append(ident)
            continue
        reason = st.split(":", 1)[1] if ":" in st else ""
        kind = GATE_KINDS.get(reason)
        # The arithmetic cut is a SUBSET of accrual, not a sibling: the status
        # says "not enough trades", and `usable_folds == 0` says no re-run can
        # ever fix it. A leg whose reach is UNGRADED stays `accrual` -- absence
        # of a graded bound is not evidence of an unreachable one.
        if (kind == "accrual" and col == "exit_head_ml"
                and row["strategy"] in arith_legs):
            kind = "arithmetic"
        buckets[kind or "unclassified"].append(ident)
    return {k: sorted(v) for k, v in buckets.items()}


def movable_cut(partition: dict[str, list]) -> dict[str, list]:
    """Split `harness_gap` + `never_attempted` into what a session can ACTUALLY move.

    ⚠️ RETURNED SEPARATELY, NOT MERGED INTO `gate_partition`. An earlier draft put
    `_movable` / `_no_sweep_path` inside the partition dict, which broke four
    pre-existing tests and deserved to: that dict is a STRICT PARTITION of the
    done-condition (`tests/test_gate_partition.py` asserts it reconciles to
    `cells_to_done` and that no cell appears in two buckets). These two lists are
    an OVERLAPPING VIEW of two of its buckets, so adding them double-counted 4
    cells and made the total read 26 against a true 22. The guard's own words:
    *"a partition that does not reconcile is worse than none, because it reads as
    exhaustive."* A cross-cut and a partition are different shapes; keep them in
    different keys.

    `harness_gap` + `never_attempted` are the two buckets whose remedy is EFFORT
    rather than waiting -- but only for a lever a session can actually sweep.
    """
    movable: list = []
    no_path: list = []
    for key in ("harness_gap", "never_attempted"):
        for ident in partition.get(key, []):
            target = no_path if ident[3] in LEVERS_WITHOUT_A_SWEEP_PATH else movable
            target.append(ident)
    return {"movable": sorted(movable), "no_sweep_path": sorted(no_path)}


def rollup(matrix: dict[str, Any]) -> dict[str, Any]:
    per_status: Counter[str] = Counter()
    per_lever: dict[str, Counter[str]] = defaultdict(Counter)
    open_cells: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    live_legs = {
        (r["strategy"], r["symbol"], r["tf"])
        for r in matrix["rows"] if r.get("execution") == "live"
    }

    # WHY the sub-status is kept. `base()` collapses
    # `blocked:insufficient_lifetime_trades` and `blocked:native-history-thin`
    # into one `blocked`, which is right for the headline arithmetic and wrong
    # for the reader: those two are different problems with different remedies,
    # and the aggregate invites "12 blocked, revisit later" over a set where
    # most will not clear on any window. The reason is already ON the cell --
    # this stops dropping it. Structural: it reports the status string the
    # matrix carries, never a claim about whether a cell can clear.
    per_lever_reason: dict[str, Counter[str]] = defaultdict(Counter)

    for row, col, status in cells(matrix):
        b = base(status) or "MISSING"
        per_status[b] += 1
        per_lever[col][b] += 1
        if b in OPEN_STATUSES:
            open_cells[b].append(
                (row["strategy"], row["symbol"], row["tf"], col))
            # Sub-status only where one exists; a bare `blocked` records itself
            # as `blocked` rather than inventing a reason it does not state.
            per_lever_reason[col][status if isinstance(status, str) else str(status)] += 1

    _reach = fold_reachability(matrix)
    _gate_part = gate_partition(matrix, _reach)
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
        "per_lever_reason": {k: dict(v) for k, v in per_lever_reason.items()},
        "fold_reachability": _reach,
        "counts": counts,
        "headline_pct": round(100 * counts["headline"] / total, 1) if total else 0.0,
        "cells_to_done": per_status["pending"] + per_status["blocked"],
        "open_cells": {k: sorted(v) for k, v in open_cells.items()},
        "matrix_updated_at": matrix.get("updated_at"),
        "evidence_vintage": evidence_vintage(matrix),
        "gate_partition": _gate_part,
        # A separate key, deliberately -- see `movable_cut`. It is an overlapping
        # VIEW of two partition buckets, so it must never live inside the
        # partition dict (which reconciles to `cells_to_done`).
        "movable_cut": movable_cut(_gate_part),
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
    # HOW MANY OF THOSE BLOCKERS CANNOT BE CLOSED BY DOING MORE WORK.
    # A done-condition that counts arithmetically-impossible cells alongside
    # runnable ones invites the reading "keep sweeping and it converges". It
    # does not: a leg with 31 lifetime trades cannot reach u>=2 (N>=150) by
    # being re-run, only by TRADING more, which is a strategy question and not
    # an M20 one. Stated here so the remaining work is legible as two different
    # kinds of remaining.
    _unreach = [x for x in (r.get("fold_reachability") or [])
                if x.get("usable_folds") == 0]
    if _unreach:
        _blocked = r["per_status"].get("blocked", 0)
        out += [
            f"    OF THE {_blocked} BLOCKED: {len(_unreach)} are exit_head_ml cells "
            f"at u=0 — ARITHMETICALLY unreachable, not un-run.",
            "      They close only if the leg trades more (a strategy question), or if",
            "      the E1 protocol changes. Re-running the sweep cannot move them, so",
            "      Re-running the sweep cannot move them.",
            "",
        ]
    # THE PARTITION, replacing a single "actionable" number.
    # This line used to read `{cells_to_done - unreachable} actionable +
    # {unreachable} arithmetic`, which subtracted ONE gate and called the other
    # four actionable. It reported 12 where ~4 were workable, and it reached a
    # roadmap entry and three operator pings before anyone read the block
    # reasons (BL-20260817-M20-ACTIONABLE-COUNT-OVERSTATES-WHAT-A-SESSION-CAN-DO).
    _gp = r.get("gate_partition") or {}
    if _gp:
        _order = ["arithmetic", "accrual", "data", "harness_gap",
                  "never_attempted", "unclassified"]
        _why = {
            "arithmetic": "no re-run can move them; the leg must TRADE more",
            "accrual": "same kind of gate, bound not proven unreachable",
            "data": "candles/history do not exist to sweep",
            "harness_gap": "the harness cannot express the lever — needs CODE",
            "never_attempted": "no sweep has been run",
            "unclassified": "⚠️ status reason not in GATE_KINDS — CLASSIFY IT",
        }
        out += ["    DONE-CONDITION BY GATE (what actually blocks each cell):"]
        for k in _order + [k for k in sorted(_gp) if k not in _order]:
            if _gp.get(k):
                out.append(f"      {len(_gp[k]):3d}  {k:<16} {_why.get(k, '')}")
        out += [
            "      ^ Keys on the cell's own STATUS string, so it cannot drift",
            "        from the matrix. It does NOT recover 'parked milestone-wide'",
            "        or 'measured, grading withheld' — those live in ref PROSE and",
            "        a tool that guessed them would be manufacturing a verdict. A",
            "        hand analysis may use them; the totals reconcile either way.",
        ]
        # ⚠️ THE MOVABLE COUNT IS MEASURED, NOT INFERRED FROM THE BUCKET NAME.
        # `never_attempted` used to read "no sweep has been run", which invites
        # "so run it"; for a lever with no sweep arm there is no run to make.
        # All four cells in these two buckets were such levers on 2026-08-17, so
        # the old wording implied FOUR workable cells over a true count of ZERO
        # (BL-20260817-ROLLUP-CALLS-A-CELL-MOVABLE-WHEN-NO-SWEEP-PATH-EXISTS).
        _mc = r.get("movable_cut") or {}
        _mv, _np = _mc.get("movable") or [], _mc.get("no_sweep_path") or []
        out += [
            f"      ⚠️ MOVABLE BY A SESSION: {len(_mv)}"
            f"   (of {len(_mv) + len(_np)} in harness_gap + never_attempted)",
        ]
        for _i in _mv:
            out.append(f"           {_i[0]:<26} {_i[1]:<9} {_i[2]:<4} {_i[3]}")
        if _np:
            out.append(f"      ⚠️ NO SWEEP PATH AT ALL: {len(_np)} — a run cannot "
                       f"move these, whatever the status implies:")
            for _i in _np:
                out.append(f"           {_i[0]:<26} {_i[1]:<9} {_i[2]:<4} {_i[3]}"
                           f"  [{_i[4]}]")
            for _lv in sorted({_i[3] for _i in _np}):
                out.append(f"           {_lv}: {LEVERS_WITHOUT_A_SWEEP_PATH[_lv]}")
        out += [
            "        `arithmetic`/`accrual`/`data` wait on trades or candles, NOT",
            "        on effort — do not plan sweeps against them expecting movement.",
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
            for _leg, _lev, status, *_rest in dec:
                k = base(status) or "?"
                by_status[k] = by_status.get(k, 0) + 1
            fund = _stale_decision_funding(dec)
            block += [
                "",
                f"      ⛔ {len(dec)} of those stale cells are NOT negatives —"
                " they are live DECISIONS:",
                "         " + " · ".join(
                    f"{s_} {n_}" for s_, n_ in
                    sorted(by_status.items(), key=lambda kv: -kv[1])),
                "         routing: " + " · ".join(
                    f"{k} {n_}" for k, n_ in sorted(fund.items())),
                "         A stale NEGATIVE costs knowledge (the lever might have"
                " passed and we would not know).",
                "         A stale SHIPPED costs MONEY ONLY WHERE THE LEG IS"
                " ROUTED TO A LIVE REAL-MONEY ACCOUNT —",
                "         read the per-row `routing` column, not this count."
                " Until 2026-08-15 this banner asserted",
                "         'a real-money leg' for EVERY row while nothing here"
                " had read accounts.yaml; it was",
                "         wrong for 3 of 4. `unresolved` means we could not"
                " look — it is NOT 'paper'.",
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
            # The declared reason, where the cell states one. Printed only when
            # it says more than the base status already did — a lever whose
            # cells carry no sub-status prints nothing extra rather than a
            # decorative echo.
            reasons = r.get("per_lever_reason", {}).get(lever, {})
            extra = {k: v for k, v in reasons.items() if ":" in k}
            for k, v in sorted(extra.items(), key=lambda kv: -kv[1]):
                out.append(f"        {v:>3} × {k}")
    reach = r.get("fold_reachability") or []
    if reach:
        out += ["", "exit_head_ml — is the block ARITHMETIC or is it waiting on data?",
                f"    Derived from each cell's `lifetime_trades` field against "
                f"`train_exit_head.fold_blocks`: folds are fixed blocks of "
                f"b={_E1_BLOCK} over the sorted trade list starting one block in, so "
                f"u = len(range(b, N-b+1, b)). The E1 gate needs u >= 2, i.e. "
                f"N >= 3b = {3 * _E1_BLOCK}; even ONE fold needs N >= {2 * _E1_BLOCK}.",
                "    THERE IS NO DATE SPLIT ON THIS LEVER — MIN_OOS_TRADES and the "
                "IS/OOS cut belong to the LEVER sweep. Moving a split cannot create "
                "folds a leg has no trades for. Tracked by",
                # KEPT ON ONE LINE. A tracking id wrapped across a string-literal
                # break reads to `check_backlog_refs` as the truncated prefix, which
                # resolves to nothing and fails the guard — correctly, since a doc
                # citing a half-id is citing a row that does not exist. This is the
                # SECOND time this session; the fix is never to wrap an id, not to
                # loosen the guard.
                "    BL-20260815-EXIT-HEAD-MATRIX-REFS-USE-THE-LEVER-SWEEPS-ARITHMETIC."]
        for row in reach:
            out.append(f"      {row['strategy']:<26} N={row['lifetime_trades']:>4}  "
                       f"u={row['usable_folds']}  needs {row['short_by']} more trade(s) "
                       f"for u>=2")
        unreachable = sum(1 for x in reach if x["usable_folds"] == 0)
        out.append(f"    {unreachable} of {len(reach)} cannot form a SINGLE fold — for "
                   f"those the block is arithmetic, not data availability.")
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
                  f"closed, not negative, and NOT REPRODUCED under the live TP "
                  f"geometry. Each row states WHY: a date older than the "
                  f"cutover is only one of the reasons, and a row can be newer "
                  f"than the cutover and still stale (default cutover "
                  f"{v['cutover']}; per-lever printed per row):")
            if not v.get("classifier_available", True):
                print("  NOT COMPUTED — the family classifier could not be "
                      "imported. This is not 'no stale decisions found'.")
            elif stale_n == 0:
                print("  (0 stale cells in the population — nothing to grade, "
                      "which is NOT the same as 'all clear')")
            elif not dec:
                print(f"  (0 of {stale_n} stale cells is a non-negative — every "
                      "one is an honest_negative)")
            _by_leg = _funding_by_leg()
            for leg, lever, status, dt, cut, why in sorted(
                    dec, key=lambda d: (d[0], d[1])):
                routing = _leg_funding(leg, _by_leg)
                print(f"    {leg:<26} {lever:<16} {str(status):<22} "
                      f"newest-ref {dt or '(undated)'}  (cutover {cut})  "
                      f"routing={routing}")
                print(f"      why: {why or '(reason not recorded)'}")
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
                n_unfixed = c.get(CORPUS_HARNESS_UNFIXED, 0)
                if n_unfixed:
                    unfixed_levers = sorted({
                        r["lever"] for r in cs["rows"]
                        if r["state"] == CORPUS_HARNESS_UNFIXED})
                    print(f"  {CORPUS_HARNESS_UNFIXED:<26} {n_unfixed:>4}  "
                          f"— ⛔ A RE-RUN IS **NOT** THE REMEDY. These sit in a "
                          f"lever whose harness never modelled the live TP "
                          f"({', '.join(unfixed_levers)}), so sweeping them "
                          f"again just writes another stale row. Fix the "
                          f"harness, THEN sweep. Deleting the lever's "
                          f"LEVER_GEOMETRY_CUTOVER entry is what marks it "
                          f"fixed, and these cells return to "
                          f"{CORPUS_NO_ROW} on their own.")
                n_noprod = c.get(CORPUS_NO_PRODUCER, 0)
                if n_noprod:
                    noprod_levers = sorted({
                        r["lever"] for r in cs["rows"]
                        if r["state"] == CORPUS_NO_PRODUCER})
                    print(f"  {CORPUS_NO_PRODUCER:<26} {n_noprod:>4}  "
                          f"— ⛔ THERE IS NOTHING TO RE-RUN. No backtest driver "
                          f"emits {', '.join(noprod_levers)}; the fleet sweep "
                          f"covers 5 of the 8 lever columns and this is not "
                          f"one of them. Building a producer (or re-doing the "
                          f"study its cells cite) is the remedy — a different "
                          f"kind of work from a sweep.")
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
