#!/usr/bin/env python3
# wiring: manual-only — an instrument a session runs before it computes a
# PACKAGE-level rate over a journal TAIL. It is not a scheduled job and nothing
# imports it on the order path. Its two questions are asked together on purpose:
# "is this package actually one fan-out?" and "could this pull have truncated
# it?" — the second cannot be bounded without the first.
"""Is an `order_package_id` a fan-out key, and can a tail pull have truncated it?

THE ROW THIS ANSWERS
--------------------
`BL-20260912-A-1000-ROW-TAIL-PULL-CHANGES-A-FANOUT-PACKAGES-MEMBERSHIP-WITHIN-HOURS-SO-A-PACKAGE-LEVEL-RATE-OVER-A-TAIL-IS-UNSTABLE-BY-CONSTRUCTION`
offers two exits: package-level analyses EXCLUDE or MARK packages truncated by
the tail edge, **or** the drift rate is measured and shown to be small enough
that they need not. Its own text says an honest negative closes it.

WHAT WAS FOUND WHILE TRYING TO MEASURE THAT
-------------------------------------------
The truncation question could not be answered until a prior one was: **an
`order_package_id` is not always a fan-out key.**

MEASURED 2026-09-13 on one live pull (`/api/diag/journal?table=trades&limit=1000`,
ids 4732–5731, 793 packages, 110 multi-row):

    multi-row packages                                            110
      account_id REPEATED — cannot be ONE cross-account fan-out     16   (14.5%)
      all accounts distinct                                         94
      ungradeable (a member row carries no account_id)               0

The 16 are one package id **reused for successive re-entries** — e.g.
`pkg-4f8140865a9a4441`, four `bybit_1` / `ict_scalp_sol_15m` / SOLUSDT longs
opened 20:12, 05:05, 08:00 and 11:07.

⚠️ **THE TWO CLASSES SEPARATE WITH NO THRESHOLD TO CHOOSE.** The 94
distinct-account packages have a `created_at` spread of **max 5.918 s** (median
2.16 s, zero above 60 s); the 16 reused ids span **3 907 s – 353 436 s** (4.1
days). The gap is ≈662× with nothing in between, so `SIMULTANEITY_WINDOW_S`
below is a separator placed inside an empty gap, not a tuned parameter.

⚠️ **CONTIGUITY IS NOT THE TEST, and a session reaching for it will be wrong on
a third of the class.** 12 of the 16 are id-non-contiguous but **4 are
contiguous**, and one reused id spans 105 ids. The account repeat is the proof;
the id layout is a symptom.

HOW TRUNCATION IS BOUNDED, AND WHAT THE BOUND RESTS ON
------------------------------------------------------
A tail pull is `ORDER BY id DESC LIMIT n` (`diag._journal_select`) — no offset,
no filter. So a missing member has an id **below** the pull's minimum. Two
measured preconditions turn that into a bound rather than a guess:

  * **density** — every id in `[min_id, max_id]` is present, so a missing member
    cannot be an interior gap;
  * **monotonicity** — id order and `created_at` order agree (measured: **0
    inversions in 999 adjacent pairs**), so a member below the edge was created
    no later than the edge row.

Given both, a package whose EARLIEST member was created more than the observed
class spread after the edge row cannot have a sibling below the edge.

⚠️ **THIS IS A BOUND FROM AN OBSERVED MAXIMUM, NOT A PROOF**, and the state name
says so: `beyond_observed_reach`, never `complete`. A fan-out wider than any in
the pull would defeat it. That is why `observed_fanout_bound` returns the bound
it used and the report prints it — a reader can see what the claim rests on.

⚠️ **AND THE REUSE CLASS CANNOT BE BOUNDED AT ALL.** Its spread is limited only
by the window the pull covers, so a reused id can straddle the edge from far
above it. Those packages grade `unbounded_class` — *we cannot say* — which is
neither at risk nor safe, and must not be pooled with either.

WHAT THIS DOES NOT DO
---------------------
⚠️ It proposes no parameter and changes no analysis. Whether a package-level
rate should EXCLUDE the reuse class is a scoping decision for whoever publishes
the rate; this file supplies the census, not the verdict.

⚠️ It cannot COMPLETE a truncated package. The surface that could —
`GET /api/bot/db/table/trades?filter_col=order_package_id` — carries
`Depends(require_session)` and returns **401** to a session holding only
`DIAG_READ_TOKEN` (measured 2026-09-13: `/api/diag/version` → 200 on the same
host with the same bearer, as the positive control). So the row's "pull by id
range rather than tail limit" candidate is not available to a research session.

⚠️ `linked_trade_id` is NOT a truncation detector and was tested as one: over
the 793 packages in the pull it pointed outside the observed member set **0 of
520** times (273 non-numeric, 0 package rows absent). It never names a member
the tail lost.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from typing import Any

# ---------------------------------------------------------------- vocabularies

#: What a multi-row `order_package_id` actually is. Never collapsed: the last
#: two are *we could not look*, and are not evidence either way.
FANOUT_STATES = (
    "simultaneous_fanout",
    "account_repeated",
    "distinct_accounts_wide_spread",
    "ungradeable_no_account",
    "ungradeable_no_timestamp",
)

#: Whether the tail edge can have truncated this package.
REACH_STATES = (
    "beyond_observed_reach",
    "within_observed_reach",
    "unbounded_class",
    "ungradeable_reach",
)

#: Whether the pull satisfies the preconditions the reach bound needs.
BASIS_STATES = ("usable", "not_monotone", "not_dense", "ungradeable")

#: Separator between the two classes, placed inside the measured 5.918 s →
#: 3 907 s gap. It is a SEPARATOR, not a tuning knob: any value in that gap
#: yields the same partition on the measured pull.
SIMULTANEITY_WINDOW_S = 60.0


# ------------------------------------------------------------------- utilities


def parse_ts(value: Any) -> float | None:
    """Epoch seconds, or ``None`` when the field cannot be read.

    ``None`` is *we could not read this timestamp* and is propagated as its own
    state upstream. It is never coerced to 0.0, which would place the row at the
    Unix epoch and make every package look impossibly wide.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # The journal writes epoch-ms in some columns and ISO-8601 in others.
        return float(value) / 1000.0 if float(value) > 1e11 else float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _row_id(row: dict) -> int | None:
    raw = row.get("id")
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def group_by_package(rows: list[dict]) -> dict[str, list[dict]]:
    """Group rows on `order_package_id`, dropping rows with no id of either kind.

    A row with no `order_package_id` belongs to no package, and a row with no
    `id` cannot be placed against the tail edge. Both are excluded from the
    grouping and counted separately by `census`, never silently folded in.
    """
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        pkg = row.get("order_package_id")
        if not pkg or _row_id(row) is None:
            continue
        out[str(pkg)].append(row)
    return dict(out)


# ------------------------------------------------------------ the two verdicts


def fanout_state(members: list[dict], window_s: float = SIMULTANEITY_WINDOW_S) -> str:
    """Grade what a multi-row package IS.

    The account-repeat test runs FIRST and deliberately needs no timestamp: a
    package holding two rows for the same account cannot be one cross-account
    fan-out whatever the clock says, so a package with unreadable timestamps is
    still provably not a fan-out. Testing simultaneity first would send those to
    `ungradeable_no_timestamp` and lose a fact we hold.
    """
    accounts = [m.get("account_id") for m in members]
    if any(a is None or str(a).strip() == "" for a in accounts):
        return "ungradeable_no_account"
    if len(set(accounts)) < len(accounts):
        return "account_repeated"
    stamps = [parse_ts(m.get("created_at")) for m in members]
    if any(s is None for s in stamps):
        return "ungradeable_no_timestamp"
    if max(stamps) - min(stamps) <= window_s:
        return "simultaneous_fanout"
    return "distinct_accounts_wide_spread"


def id_monotonicity(rows: list[dict]) -> tuple[str, dict]:
    """Do id order and `created_at` order agree? Returns a state and its terms."""
    pairs = []
    unreadable = 0
    for row in rows:
        rid, stamp = _row_id(row), parse_ts(row.get("created_at"))
        if rid is None or stamp is None:
            unreadable += 1
            continue
        pairs.append((rid, stamp))
    terms = {"pairs_graded": len(pairs), "rows_unreadable": unreadable}
    if len(pairs) < 2:
        terms["inversions"] = None
        return "ungradeable", terms
    pairs.sort(key=lambda p: p[0])
    inversions = sum(1 for i in range(1, len(pairs)) if pairs[i][1] < pairs[i - 1][1])
    terms["inversions"] = inversions
    terms["adjacent_pairs"] = len(pairs) - 1
    return ("monotone" if inversions == 0 else "inverted"), terms


def id_density(rows: list[dict]) -> tuple[str, dict]:
    """Is every id between the pull's min and max present?"""
    ids = sorted({r for r in (_row_id(x) for x in rows) if r is not None})
    if len(ids) < 2:
        return "ungradeable", {"ids_graded": len(ids), "gaps": None}
    span = ids[-1] - ids[0] + 1
    gaps = span - len(ids)
    terms = {"ids_graded": len(ids), "min_id": ids[0], "max_id": ids[-1], "gaps": gaps}
    return ("dense" if gaps == 0 else "gapped"), terms


def basis_state(rows: list[dict]) -> tuple[str, dict]:
    """Can the reach bound be computed on this pull at all?

    Both preconditions are REQUIRED, and a failure of either makes every reach
    verdict `ungradeable_reach` rather than merely less precise — a bound
    derived from an assumption the pull violates is worse than no bound.
    """
    mono, mono_terms = id_monotonicity(rows)
    dense, dense_terms = id_density(rows)
    terms = {"monotonicity": mono, "density": dense, **mono_terms, **dense_terms}
    if mono == "ungradeable" or dense == "ungradeable":
        return "ungradeable", terms
    if mono == "inverted":
        return "not_monotone", terms
    if dense == "gapped":
        return "not_dense", terms
    return "usable", terms


def observed_fanout_bound(
    packages: dict[str, list[dict]], window_s: float = SIMULTANEITY_WINDOW_S
) -> float | None:
    """The widest `created_at` spread among packages graded `simultaneous_fanout`.

    ``None`` when no package graded that way — the bound does not EXIST, which
    is a different fact from a bound of 0.0 s. A 0.0 s bound would declare every
    package beyond reach and silently report a clean pull.
    """
    spreads = []
    for members in packages.values():
        if len(members) < 2:
            continue
        if fanout_state(members, window_s) != "simultaneous_fanout":
            continue
        stamps = [parse_ts(m.get("created_at")) for m in members]
        spreads.append(max(stamps) - min(stamps))
    return max(spreads) if spreads else None


def reach_state(
    members: list[dict],
    *,
    edge_ts: float | None,
    bound_s: float | None,
    basis: str,
    fanout: str,
) -> str:
    """Could the tail edge have truncated this package?

    A single-row package is graded too, and must be: a truncated fan-out's
    surviving remnant is a single row, so exempting singles would hide exactly
    the case the row is about.
    """
    if basis != "usable" or edge_ts is None or bound_s is None:
        return "ungradeable_reach"
    if fanout in ("account_repeated", "distinct_accounts_wide_spread"):
        # Spread limited only by the window the pull covers.
        return "unbounded_class"
    if fanout in ("ungradeable_no_account", "ungradeable_no_timestamp"):
        return "ungradeable_reach"
    stamps = [parse_ts(m.get("created_at")) for m in members]
    if any(s is None for s in stamps):
        return "ungradeable_reach"
    return "beyond_observed_reach" if min(stamps) > edge_ts + bound_s else "within_observed_reach"


# ------------------------------------------------------------------- the census


def census(rows: list[dict], window_s: float = SIMULTANEITY_WINDOW_S) -> dict:
    """Grade a whole pull. Every state in both vocabularies is reported."""
    packages = group_by_package(rows)
    basis, basis_terms = basis_state(rows)

    ids = sorted({r for r in (_row_id(x) for x in rows) if r is not None})
    edge_ts = None
    if ids:
        by_id = {_row_id(r): r for r in rows if _row_id(r) is not None}
        edge_ts = parse_ts(by_id[ids[0]].get("created_at"))

    bound = observed_fanout_bound(packages, window_s)

    fan_counts = {s: 0 for s in FANOUT_STATES}
    reach_counts = {s: 0 for s in REACH_STATES}
    per_package: dict[str, dict] = {}
    at_risk: list[str] = []
    reuse: list[str] = []

    for pkg, members in packages.items():
        fan = fanout_state(members, window_s) if len(members) > 1 else None
        if fan is not None:
            fan_counts[fan] += 1
        # A single-row package has no fan-out verdict but still has a reach one;
        # grade it through the simultaneous branch, which is the only class it
        # could be a remnant of.
        reach = reach_state(
            members,
            edge_ts=edge_ts,
            bound_s=bound,
            basis=basis,
            fanout=fan if fan is not None else "simultaneous_fanout",
        )
        reach_counts[reach] += 1
        per_package[pkg] = {
            "n_rows": len(members),
            "fanout_state": fan,
            "reach_state": reach,
            "ids": sorted(_row_id(m) for m in members),
        }
        if reach == "within_observed_reach":
            at_risk.append(pkg)
        if fan == "account_repeated":
            reuse.append(pkg)

    multi = sum(1 for m in packages.values() if len(m) > 1)
    rows_in_reuse = sum(len(packages[p]) for p in reuse)
    rows_in_multi = sum(len(m) for m in packages.values() if len(m) > 1)

    return {
        "population": {
            "rows_read": len(rows),
            "rows_without_package_or_id": len(rows) - sum(len(m) for m in packages.values()),
            "packages": len(packages),
            "multi_row_packages": multi,
            "rows_in_multi_row_packages": rows_in_multi,
        },
        "basis": {"state": basis, **basis_terms},
        "simultaneity_window_s": window_s,
        "observed_fanout_bound_s": bound,
        "edge_created_at_epoch": edge_ts,
        "fanout_states": fan_counts,
        "reach_states": reach_counts,
        "reuse_packages": sorted(reuse),
        "rows_in_reuse_packages": rows_in_reuse,
        "at_risk_packages": sorted(at_risk),
        "per_package": per_package,
    }


def report(result: dict) -> list[str]:
    """Render the census with its population attached to every number."""
    pop = result["population"]
    out = [
        "package fan-out integrity",
        "  POPULATION: %d rows read; %d carried no order_package_id or no id and are "
        "excluded from grouping; %d packages, of which %d are multi-row (%d rows)."
        % (
            pop["rows_read"],
            pop["rows_without_package_or_id"],
            pop["packages"],
            pop["multi_row_packages"],
            pop["rows_in_multi_row_packages"],
        ),
        "  BASIS: %s (monotonicity=%s, inversions=%s over %s adjacent pairs; density=%s, "
        "gaps=%s over %s ids)"
        % (
            result["basis"]["state"],
            result["basis"].get("monotonicity"),
            result["basis"].get("inversions"),
            result["basis"].get("adjacent_pairs"),
            result["basis"].get("density"),
            result["basis"].get("gaps"),
            result["basis"].get("ids_graded"),
        ),
    ]
    if result["basis"]["state"] != "usable":
        out.append(
            "  ⚠️ THE REACH BOUND IS NOT COMPUTABLE ON THIS PULL — every package grades "
            "ungradeable_reach. That is 'we could not look', NOT 'nothing is truncated'."
        )
    bound = result["observed_fanout_bound_s"]
    out.append(
        "  BOUND: %s — the widest created_at spread among packages graded "
        "simultaneous_fanout in THIS pull. It is an observed maximum, not a guarantee."
        % ("no simultaneous_fanout package exists, so no bound EXISTS" if bound is None
           else "%.3fs" % bound)
    )
    out.append("  what a multi-row package IS (denominator %d):" % pop["multi_row_packages"])
    for state in FANOUT_STATES:
        out.append("      %-30s %4d" % (state, result["fanout_states"][state]))
    out.append("  could the tail edge have truncated it (denominator %d packages):" % pop["packages"])
    for state in REACH_STATES:
        out.append("      %-30s %4d" % (state, result["reach_states"][state]))
    n_reuse = result["fanout_states"]["account_repeated"]
    if n_reuse:
        out.append(
            "  ⚠️ %d package(s) holding %d rows repeat an account_id and CANNOT be one "
            "cross-account fan-out; they are one id reused across re-entries. A "
            "package-level rate treats them as a single unit."
            % (n_reuse, result["rows_in_reuse_packages"])
        )
    return out


# ---------------------------------------------------------------------- fixtures


def _row(rid, pkg, acct, created, **extra):
    row = {"id": rid, "order_package_id": pkg, "account_id": acct, "created_at": created}
    row.update(extra)
    return row


def _dense_pull() -> list[dict]:
    """Ten dense, monotone ids: one 3-account fan-out, one reused id, singles."""
    base = 1_700_000_000
    rows = [
        _row(100, "pkg-fan", "a1", base + 0.0),
        _row(101, "pkg-fan", "a2", base + 1.0),
        _row(102, "pkg-fan", "a3", base + 2.0),
        _row(103, "pkg-reuse", "a1", base + 10.0),
        _row(104, "pkg-solo1", "a1", base + 20.0),
        _row(105, "pkg-solo2", "a2", base + 30.0),
        _row(106, "pkg-reuse", "a1", base + 40_000.0),
        _row(107, "pkg-solo3", "a3", base + 40_010.0),
        _row(108, "pkg-wide", "a1", base + 50_000.0),
        _row(109, "pkg-wide", "a2", base + 90_000.0),
    ]
    return rows


# --------------------------------------------------------------------- self-test


def self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def ok(name, cond):
        checks.append((name, bool(cond)))

    base = 1_700_000_000
    pull = _dense_pull()

    # --- parse_ts -----------------------------------------------------------
    ok("parse_ts reads ISO-8601", parse_ts("2026-09-13T00:00:00+00:00") is not None)
    ok("parse_ts reads a Z suffix", parse_ts("2026-09-13T00:00:00Z") is not None)
    ok("parse_ts reads epoch seconds", parse_ts(1_700_000_000) == 1_700_000_000.0)
    ok("parse_ts reads epoch millis", parse_ts(1_700_000_000_000) == 1_700_000_000.0)
    ok("parse_ts None -> None, never 0.0", parse_ts(None) is None)
    ok("parse_ts empty -> None, never 0.0", parse_ts("") is None)
    ok("parse_ts garbage -> None, never 0.0", parse_ts("not-a-date") is None)
    ok("parse_ts refuses a bool", parse_ts(True) is None)

    # --- grouping -----------------------------------------------------------
    g = group_by_package(pull)
    ok("grouping finds every package", set(g) == {"pkg-fan", "pkg-reuse", "pkg-solo1",
                                                  "pkg-solo2", "pkg-solo3", "pkg-wide"})
    ok("grouping keeps all 3 fan-out members", len(g["pkg-fan"]) == 3)
    ok("a row with no package id is dropped from grouping",
       len(group_by_package(pull + [_row(110, None, "a1", base + 99)])) == len(g))
    ok("a row with no id is dropped from grouping",
       len(group_by_package([_row(None, "pkg-x", "a1", base)])) == 0)

    # --- fanout_state -------------------------------------------------------
    ok("3 distinct accounts within the window -> simultaneous_fanout",
       fanout_state(g["pkg-fan"]) == "simultaneous_fanout")
    ok("a repeated account -> account_repeated",
       fanout_state(g["pkg-reuse"]) == "account_repeated")
    ok("distinct accounts far apart -> distinct_accounts_wide_spread",
       fanout_state(g["pkg-wide"]) == "distinct_accounts_wide_spread")
    ok("a missing account_id -> ungradeable_no_account",
       fanout_state([_row(1, "p", None, base), _row(2, "p", "a2", base)])
       == "ungradeable_no_account")
    ok("an empty account_id is ungradeable, not a distinct account",
       fanout_state([_row(1, "p", "  ", base), _row(2, "p", "a2", base)])
       == "ungradeable_no_account")
    ok("an unreadable timestamp on distinct accounts -> ungradeable_no_timestamp",
       fanout_state([_row(1, "p", "a1", None), _row(2, "p", "a2", base)])
       == "ungradeable_no_timestamp")
    ok("THE ORDER OF THE TESTS: a repeated account is proven WITHOUT a timestamp",
       fanout_state([_row(1, "p", "a1", None), _row(2, "p", "a1", None)])
       == "account_repeated")
    ok("every fanout_state returned is in the declared vocabulary",
       all(fanout_state(m) in FANOUT_STATES for m in g.values() if len(m) > 1))

    # --- contiguity is NOT the test -----------------------------------------
    contiguous_reuse = [_row(200, "p", "a1", base), _row(201, "p", "a1", base + 9_000)]
    ok("a CONTIGUOUS pair that repeats an account is still account_repeated",
       fanout_state(contiguous_reuse) == "account_repeated")
    noncontig_fanout = [_row(300, "p", "a1", base), _row(305, "p", "a2", base + 1)]
    ok("a NON-CONTIGUOUS pair with distinct accounts is still a fan-out",
       fanout_state(noncontig_fanout) == "simultaneous_fanout")

    # --- basis --------------------------------------------------------------
    st, terms = id_monotonicity(pull)
    ok("a monotone pull grades monotone", st == "monotone")
    ok("monotonicity reports its denominator", terms["adjacent_pairs"] == 9)
    inverted = [_row(1, "p", "a1", base + 100), _row(2, "q", "a1", base + 1)]
    ok("an inverted pull grades inverted", id_monotonicity(inverted)[0] == "inverted")
    ok("fewer than 2 gradeable pairs -> ungradeable, never monotone",
       id_monotonicity([_row(1, "p", "a1", base)])[0] == "ungradeable")
    ok("inversions is None when ungradeable, never 0",
       id_monotonicity([_row(1, "p", "a1", base)])[1]["inversions"] is None)
    ok("a dense pull grades dense", id_density(pull)[0] == "dense")
    ok("a gapped pull grades gapped",
       id_density([_row(1, "p", "a1", base), _row(5, "q", "a1", base + 1)])[0] == "gapped")
    ok("basis on the fixture is usable", basis_state(pull)[0] == "usable")
    ok("an inverted pull makes the basis not_monotone", basis_state(inverted)[0] == "not_monotone")
    ok("a gapped pull makes the basis not_dense",
       basis_state([_row(1, "p", "a1", base), _row(5, "q", "a1", base + 1)])[0] == "not_dense")

    # --- the bound ----------------------------------------------------------
    b = observed_fanout_bound(g)
    ok("the bound is the widest simultaneous_fanout spread", b == 2.0)
    ok("no simultaneous_fanout package -> the bound is None, NEVER 0.0",
       observed_fanout_bound(group_by_package(
           [_row(1, "p", "a1", base), _row(2, "p", "a1", base + 9_000)])) is None)

    # --- reach --------------------------------------------------------------
    c = census(pull)
    ok("the edge package is within_observed_reach",
       c["per_package"]["pkg-fan"]["reach_state"] == "within_observed_reach")
    ok("a package well above the edge is beyond_observed_reach",
       c["per_package"]["pkg-solo3"]["reach_state"] == "beyond_observed_reach")
    ok("the reuse package is unbounded_class, neither safe nor at risk",
       c["per_package"]["pkg-reuse"]["reach_state"] == "unbounded_class")
    ok("a wide distinct-account package is also unbounded_class",
       c["per_package"]["pkg-wide"]["reach_state"] == "unbounded_class")
    ok("SINGLE-row packages are graded for reach too",
       c["per_package"]["pkg-solo1"]["reach_state"] in REACH_STATES)
    ok("an unusable basis makes EVERY package ungradeable_reach",
       set(p["reach_state"] for p in census(inverted)["per_package"].values())
       == {"ungradeable_reach"})
    # A pull that is GAPPED but otherwise fully gradeable: a real fan-out gives
    # it a bound and an edge, so ONLY the basis gate can refuse it. Without this
    # the basis check can be deleted and the `inverted` fixture still passes,
    # because that one has no bound either.
    gapped_but_bounded = [
        _row(1, "pkg-f", "a1", base + 0.0),
        _row(2, "pkg-f", "a2", base + 1.0),
        _row(9, "pkg-late", "a1", base + 900.0),
    ]
    ok("the gapped fixture really is gradeable apart from the basis",
       observed_fanout_bound(group_by_package(gapped_but_bounded)) == 1.0)
    ok("a GAPPED pull refuses every reach verdict even though a bound exists",
       set(p["reach_state"] for p in census(gapped_but_bounded)["per_package"].values())
       == {"ungradeable_reach"})
    ok("a None bound makes reach ungradeable, never beyond",
       reach_state(g["pkg-fan"], edge_ts=base, bound_s=None, basis="usable",
                   fanout="simultaneous_fanout") == "ungradeable_reach")
    ok("every reach_state returned is in the declared vocabulary",
       all(p["reach_state"] in REACH_STATES for p in c["per_package"].values()))

    # --- census + report ----------------------------------------------------
    ok("the census reports EVERY fanout state, present or not",
       set(c["fanout_states"]) == set(FANOUT_STATES))
    ok("the census reports EVERY reach state, present or not",
       set(c["reach_states"]) == set(REACH_STATES))
    ok("reach counts sum to the package count",
       sum(c["reach_states"].values()) == c["population"]["packages"])
    ok("fanout counts sum to the MULTI-ROW package count",
       sum(c["fanout_states"].values()) == c["population"]["multi_row_packages"])
    ok("the reuse row count is reported", c["rows_in_reuse_packages"] == 2)
    ok("a row with no package id is counted, not silently dropped",
       census(pull + [_row(110, None, "a1", base + 99)])
       ["population"]["rows_without_package_or_id"] == 1)
    text = "\n".join(report(c))
    ok("the report PRINTS every fanout state, including the ZERO ones",
       all(state in text for state in FANOUT_STATES))
    ok("the report PRINTS every reach state, including the ZERO ones",
       all(state in text for state in REACH_STATES))
    ok("a zero count is rendered as 0 and not omitted",
       c["fanout_states"]["ungradeable_no_account"] == 0
       and "ungradeable_no_account" in text)
    ok("the report states the multi-row denominator", "denominator 3)" in text)
    ok("the report states the package denominator", "denominator 6 packages" in text)
    ok("the report names the bound it used", "2.000s" in text)
    ok("the report warns when the basis is unusable",
       "REACH BOUND IS NOT COMPUTABLE" in "\n".join(report(census(inverted))))
    ok("that warning says 'we could not look', not 'nothing is truncated'",
       "NOT 'nothing is truncated'" in "\n".join(report(census(inverted))))
    ok("the report says a missing bound does not EXIST rather than printing 0",
       "no bound EXISTS" in "\n".join(report(census(
           [_row(1, "p", "a1", base), _row(2, "p", "a1", base + 9_000)]))))

    # --- cross-check reporting ---------------------------------------------
    # The file-driven path is exercised against the live pull in the memo; what
    # is asserted here is that each STATE renders as the distinct thing it is,
    # because the failure that matters is a zero delta reading as a clean result.
    no_overlap = {"state": "no_overlap_cannot_verify",
                  "positive_control": {"unit_packages": 40, "recognised_in_pull": 0},
                  "reuse_packages_in_pull": 16}
    no_reuse = {"state": "no_reuse_in_cells",
                "positive_control": {"unit_packages": 40, "recognised_in_pull": 40},
                "reuse_units_per_cell": {"treated_pre": 0, "control_post": 0},
                "reuse_packages_in_pull": 16}
    ok("every cross-check state is in the declared vocabulary",
       all(d["state"] in CROSSCHECK_STATES for d in (no_overlap, no_reuse)))
    t1 = "\n".join(cross_check_report(no_overlap))
    ok("no_overlap says 'we could not look', NOT 'no effect'",
       "NOT 'no effect'" in t1)
    ok("no_overlap prints its positive control",
       "0 of 40" in t1)
    t2 = "\n".join(cross_check_report(no_reuse))
    ok("no_reuse_in_cells is rendered as its own outcome, not as no_overlap",
       "No reuse package falls inside" in t2 and "could not look" not in t2)
    ok("no_reuse_in_cells still prints its positive control", "40 of 40" in t2)
    graded = {"state": "graded",
              "positive_control": {"unit_packages": 168, "recognised_in_pull": 168},
              "reuse_packages_in_pull": 16,
              "reuse_units_per_cell": {"treated_pre": 0, "treated_post": 0,
                                       "control_pre": 0, "control_post": 10},
              "band_with_reuse": {"did_low": 0.038, "did_high": 0.296, "sign_survives": True,
                                  "cells": {c: {"denominator": 1, "disagreement": 1}
                                            for c in ("treated_pre", "treated_post",
                                                      "control_pre", "control_post")}},
              "band_without_reuse": {"did_low": 0.061, "did_high": 0.237, "sign_survives": True,
                                     "cells": {c: {"denominator": 1, "disagreement": 0}
                                               for c in ("treated_pre", "treated_post",
                                                         "control_pre", "control_post")}}}
    t3 = "\n".join(cross_check_report(graded))
    ok("graded prints BOTH bands, never only the excluded one",
       "3.8..29.6" in t3 and "6.1..23.7" in t3)
    ok("graded prints each band's WIDTH, which is what the reuse class moves",
       "width 25.8 pp" in t3 and "width 17.6 pp" in t3)
    ok("graded prints the per-cell disagreement change", "disagreements  1 ->  0" in t3)
    ok("graded refuses to propose an exclusion", "NO EXCLUSION IS PROPOSED" in t3)

    failed = [n for n, good in checks if not good]
    for name, good in checks:
        print("%s  %s" % ("ok  " if good else "FAIL", name))
    print("\nself-test: %d checks, %d passed, %d failed (denominator = %d)"
          % (len(checks), len(checks) - len(failed), len(failed), len(checks)))
    return 1 if failed else 0


# ------------------------------------------------------- cross-check against U15

#: Whether the cross-check could be performed at all.
CROSSCHECK_STATES = ("graded", "no_overlap_cannot_verify", "no_reuse_in_cells")


def cross_check_u15(trades_path: str, pkgs_path: str, tol: float = 0.001) -> dict:
    """What does the reuse class do to MI-278 U15's published DiD band?

    Imports U15's OWN arm splitter and U22's OWN band, both unmodified, and
    varies exactly one thing: whether packages graded `account_repeated` are in
    the cells. A difference is therefore attributable to the GROUPING KEY and to
    nothing else — the discipline U19 used on U18 and U22 used on U15.

    ⚠️ A POSITIVE CONTROL RUNS FIRST. If the arm builder's package ids do not
    intersect this pull's packages, the reuse set cannot match anything and a
    difference of zero would be a false negative dressed as a clean result. That
    case returns `no_overlap_cannot_verify`, never `graded` with a zero delta.

    ⚠️ THIS PROPOSES NO EXCLUSION. It reports what the choice is worth. Whether a
    reuse package belongs in a fan-out study is the publisher's decision.
    """
    sys.path.insert(0, "scripts")
    from research.fanout_exit_unit import did_band
    from research.stop_integrity_both_arms import build

    with open(trades_path, encoding="utf-8") as fh:
        trades = json.load(fh)
    with open(pkgs_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    pk_rows = raw["rows"] if isinstance(raw, dict) and "rows" in raw else raw
    pkgs = {p["order_package_id"]: p for p in pk_rows if p.get("order_package_id")}
    by_id = {r["id"]: r for r in trades}

    result = census(trades)
    reuse = set(result["reuse_packages"])

    cells: dict[str, list[dict]] = {}
    for group, arm in (("e35", "treated"), ("untouched_control", "control")):
        built = build(trades, pkgs, group, tol)
        for era in ("pre", "post"):
            cells["%s_%s" % (arm, era)] = built[era]

    known = set(result["per_package"])
    unit_pkgs = [u["package"] for v in cells.values() for u in v]
    overlap = sum(1 for u in unit_pkgs if u in known)
    control = {"unit_packages": len(unit_pkgs), "recognised_in_pull": overlap}
    if unit_pkgs and overlap == 0:
        return {"state": "no_overlap_cannot_verify", "positive_control": control,
                "reuse_packages_in_pull": len(reuse)}

    def band(exclude: set) -> dict:
        rows = {k: [by_id[t] for u in v if u["package"] not in exclude
                    for t in u["trade_ids"] if t in by_id]
                for k, v in cells.items()}
        return did_band(rows["treated_pre"], rows["treated_post"],
                        rows["control_pre"], rows["control_post"], tol)

    per_cell = {k: sum(1 for u in v if u["package"] in reuse) for k, v in cells.items()}
    if not any(per_cell.values()):
        return {"state": "no_reuse_in_cells", "positive_control": control,
                "reuse_units_per_cell": per_cell, "reuse_packages_in_pull": len(reuse)}

    with_all, without = band(set()), band(reuse)
    return {
        "state": "graded",
        "positive_control": control,
        "reuse_packages_in_pull": len(reuse),
        "reuse_units_per_cell": per_cell,
        "band_with_reuse": with_all,
        "band_without_reuse": without,
        "tol": tol,
    }


def cross_check_report(cc: dict) -> list[str]:
    out = ["cross-check against MI-278 U15's DiD band  (state: %s)" % cc["state"]]
    ctl = cc.get("positive_control", {})
    out.append("  POSITIVE CONTROL: %s of %s arm-builder unit packages are recognised in this pull."
               % (ctl.get("recognised_in_pull"), ctl.get("unit_packages")))
    if cc["state"] == "no_overlap_cannot_verify":
        out.append("  ⚠️ The reuse set cannot match anything here — 'we could not look', NOT 'no effect'.")
        return out
    out.append("  reuse units per cell: %s" % cc["reuse_units_per_cell"])
    if cc["state"] == "no_reuse_in_cells":
        out.append("  No reuse package falls inside U15's cells on this pull, so the band is unchanged.")
        return out
    for label, key in (("with reuse   ", "band_with_reuse"), ("reuse removed", "band_without_reuse")):
        b = cc[key]
        out.append("  %s : %.1f..%.1f pp  (width %.1f pp, sign_survives=%s)"
                   % (label, b["did_low"] * 100, b["did_high"] * 100,
                      (b["did_high"] - b["did_low"]) * 100, b["sign_survives"]))
    for cell in ("treated_pre", "treated_post", "control_pre", "control_post"):
        a = cc["band_with_reuse"]["cells"][cell]
        z = cc["band_without_reuse"]["cells"][cell]
        out.append("      %-14s n %3d -> %3d   disagreements %2d -> %2d"
                   % (cell, a["denominator"], z["denominator"],
                      a["disagreement"], z["disagreement"]))
    out.append("  ⚠️ NO EXCLUSION IS PROPOSED. This states what the grouping-key choice is worth.")
    return out


# --------------------------------------------------------------------------- cli


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trades", help="path to a saved /api/diag/journal?table=trades JSON array")
    ap.add_argument("--window", type=float, default=SIMULTANEITY_WINDOW_S,
                    help="simultaneity separator in seconds (default %(default)s)")
    ap.add_argument("--json", action="store_true", help="emit the census as JSON")
    ap.add_argument("--packages", help="path to a saved order_packages pull, for --cross-check-u15")
    ap.add_argument("--cross-check-u15", action="store_true",
                    help="what the reuse class does to MI-278 U15's DiD band")
    ap.add_argument("--tol", type=float, default=0.001)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.trades:
        ap.error("--trades is required unless --self-test is given")

    with open(args.trades, encoding="utf-8") as fh:
        rows = json.load(fh)
    if not isinstance(rows, list):
        print("input is not a JSON array of trade rows", file=sys.stderr)
        return 2

    if args.cross_check_u15:
        if not args.packages:
            ap.error("--cross-check-u15 needs --packages")
        cc = cross_check_u15(args.trades, args.packages, args.tol)
        print(json.dumps(cc, indent=2, sort_keys=True, default=str) if args.json
              else "\n".join(cross_check_report(cc)))
        return 0

    result = census(rows, args.window)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print("\n".join(report(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
