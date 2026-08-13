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
        "cutover": GEOMETRY_CUTOVER,
        "affected_families": sorted(TP_PARITY_AFFECTED_FAMILIES),
        "classifier_available": True,
        "pre_cutover": 0, "post_cutover": 0, "undated": 0,
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
        # Base rate so far is 1 of 1: `trend_donchian` `trail_decay` is the only
        # one re-swept, and it did NOT reproduce (BL-20260808, now
        # `shipped_gate_failed`).
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
            dates = _DATE.findall(cell.get("ref") or "")
            stale = (not dates) or max(dates) < GEOMETRY_CUTOVER
            if not dates:
                out["undated"] += 1
            elif stale:
                out["pre_cutover"] += 1
                out["stale_cells"].append(
                    (row["strategy"], row["symbol"], row["tf"], col, max(dates)))
            else:
                out["post_cutover"] += 1
            # A stale cell that is not a negative is a live DECISION resting on
            # unreproduced evidence. Keyed on "not honest_negative" rather than
            # on a shipped-list, so a status added to the legend later cannot
            # silently fall out of this set.
            if stale and base(cell.get("status")) != "honest_negative":
                out["stale_decisions"].append(
                    (row["strategy"], col, cell.get("status"),
                     max(dates) if dates else None))
    return out


def base(status: str | None) -> str:
    """`blocked:data_missing` -> `blocked`. None stays None (an error)."""
    return status.split(":", 1)[0] if isinstance(status, str) else status


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
        "status counts:",
    ]
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
            f"      NO take-profit were measured BEFORE {v['cutover']}"
            f" ({v['undated']} more carry no date at all).",
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
            for _leg, _lev, status, _dt in dec:
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
                "         Base rate so far is 1 of 1: `trend_donchian`"
                " `trail_decay` is the only one re-swept",
                "         and it did NOT reproduce (now `shipped_gate_failed`)."
                "  List them with `--stale-decisions`.",
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
                  f"closed, not negative, evidence older than {v['cutover']}:")
            if not v.get("classifier_available", True):
                print("  NOT COMPUTED — the family classifier could not be "
                      "imported. This is not 'no stale decisions found'.")
            elif stale_n == 0:
                print("  (0 stale cells in the population — nothing to grade, "
                      "which is NOT the same as 'all clear')")
            elif not dec:
                print(f"  (0 of {stale_n} stale cells is a non-negative — every "
                      "one is an honest_negative)")
            for leg, lever, status, dt in sorted(dec):
                print(f"    {leg:<26} {lever:<16} {str(status):<22} "
                      f"newest-ref {dt or '(undated)'}")
        if problems:
            print(f"\n⚠️  {len(problems)} structural defect(s) — run --validate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
