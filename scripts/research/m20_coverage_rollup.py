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

* The **progress headline** (``319/376``, the figure PR #8712 established and
  the one to keep quoting) counts ``blocked`` as closed. A blocked cell is not
  work anyone can do — it is gated on data that does not exist — so leaving it
  in the numerator's complement would make the percentage a measure of the
  data backlog rather than of exit-refinement progress.
* The **done-condition** does not. The skill states it exactly:
  *"The milestone/health view of 'are we done' = no ``pending``/``blocked``
  rows on live legs."*

Therefore **M20 needs 61 cells resolved, not 57** — the 57 ``pending`` plus
the 4 ``blocked``. A session reading only the pending count will under-scope
the milestone by four cells and, more importantly, will never revisit the
blocked ones, which is how a ``blocked:data_missing`` row becomes permanent.
``--done-condition`` prints that set explicitly.

POPULATION
----------
Denominator is **live legs only** (``execution == "live"``) × the declared
``lever_columns`` — 47 × 8 = 376. Shadow and disabled legs are excluded from
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
            if not dates:
                out["undated"] += 1
            elif max(dates) < GEOMETRY_CUTOVER:
                out["pre_cutover"] += 1
                out["stale_cells"].append(
                    (row["strategy"], row["symbol"], row["tf"], col, max(dates)))
            else:
                out["post_cutover"] += 1
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
        out[2:2] = [
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
        if problems:
            print(f"\n⚠️  {len(problems)} structural defect(s) — run --validate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
