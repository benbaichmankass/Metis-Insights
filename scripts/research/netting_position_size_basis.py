#!/usr/bin/env python3
# wiring: manual-only — a one-shot audit answering "is a journal row's
#   position_size an OBSERVED quantity or one ASSIGNED by netting attribution?".
#   Run BY A SESSION against a journal pull; scheduling it would re-ask a
#   question over a window nobody chose.
"""MI-278 U33 — is `position_size` observed, or assigned by netting attribution?

THE CANDIDATE THIS TESTS, AND WHO SAID IT WAS UNTESTED
-------------------------------------------------------
`BL-20260912-THE-NETTING-ATTRIBUTED-PATH-CARRIES-THE-LARGEST-SHARE-OF-THE-WINNER-COLLAPSE-AND-IS-100-PERCENT-ESTIMATED`
names a candidate explanation and says in terms that establishing it is a
different unit:

    a netting_attributed row's position_size is ASSIGNED by
    _reconcile_netting_partial_closes rather than read from a venue fill, so
    there is no reason it must correspond to any venue close or sum of them.

It is confirmed at the field, not inferred from the prose. In
`order_monitor._netting_rows_to_attribute`, `take = min(qty, remaining)` where
`remaining` starts as `excess = journal_qty - backed` — a subtraction of two
AGGREGATES, distributed over rows by a selection heuristic (`leg_gone`, then
FIFO). No venue fill quantity enters at any point.

⚠️ AND THE FIELD SAYS SOMETHING THE CANDIDATE DID NOT
------------------------------------------------------
`_netting_apply_close`'s **partial** branch does not close the row. It writes
`position_size = qty - take` and leaves it OPEN. So a row can have its quantity
rewritten by attribution and then be closed LATER by an ordinary exit — with
`exit_reason` reading `sl_cross`, `tp_cross` or anything else.

**Every analysis of this contamination so far has scoped it by
`exit_reason == 'netting_attributed'`** — MI-277's annex A6, MI-278 U9, U16.
That filter cannot see a reduced-then-stopped-out row. This module measures the
population it misses.

⚠️ THE EVIDENCE IS DROPPABLE, WHICH IS WHY THERE IS A FOURTH STATE
-------------------------------------------------------------------
`netting_attribution_basis` and `netting_attributed_qty` live in `notes`, which
is written through `dump_capped(notes, 500)`, and NEITHER key is in
`json_notes._DEFAULT_PROTECTED`. That tuple's own comments record THREE prior
instances of a load-bearing key being shed by the cap. So a row whose notes read
`_truncated: true` with no attribution key is ***we could not look***, never
*not attributed* — a distinct state, and on the live pull it is the LARGEST of
the three.

WHAT IT DOES NOT DO
-------------------
It changes nothing. `src/runtime/order_monitor.py` is Tier-2 and is read only.
It does NOT claim any close is false — that needs the venue-side read
`OI-20260908` specifies, and option (b) of the parent row is exhausted (U9 + U16).
It reports which rows carry an assigned quantity and which of them are invisible
to the filter the prior work used.

Usage:
  python3 scripts/research/netting_position_size_basis.py --self-test
  python3 scripts/research/netting_position_size_basis.py --journal j.json
  python3 scripts/research/netting_position_size_basis.py --journal j.json --json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

#: The two `notes` keys `_netting_apply_close` stamps. BOTH are checked because
#: `dump_capped` sheds keys ONE AT A TIME by serialized length, so a row can
#: retain one and lose the other.
ATTRIBUTION_KEYS = ("netting_attribution_basis", "netting_attributed_qty")

#: The `exit_reason` the prior work scoped this contamination by.
SCOPED_EXIT_REASON = "netting_attributed"

#: How a row's `position_size` came to hold the value it holds.
QTY_BASIS_STATES = (
    "assigned_by_attribution",  # a stamp survives in notes
    "observed",                 # notes readable, complete, and carry no stamp
    "evidence_may_be_shed",     # notes are `_truncated` and carry no stamp —
                                # WE COULD NOT LOOK, never "not attributed"
    "notes_unreadable",         # absent, non-JSON, or not an object
)

#: Whether the `exit_reason` filter the prior work used can SEE this row.
VISIBILITY_STATES = (
    "visible_via_exit_reason",          # attributed and labelled as such
    "invisible_to_exit_reason_filter",  # attributed and labelled something else
    "not_attributed",
    "cannot_grade",                     # the basis itself is unknown
)


#: Two-source grading. The journal `notes` stamp and the attribution soak are
#: INDEPENDENT: the soak is written before the DB is touched and is not subject
#: to the 500-char notes cap, so it can rescue a row whose stamp was shed.
CORROBORATION_STATES = (
    "stamp_and_soak",     # both sources say the row was reduced
    "stamp_only",         # a stamp survives; the soak tail does not reach it
    "soak_only",          # THE FINDING — the soak applied it and the stamp is gone
    "neither",            # no evidence from either source
    "soak_not_supplied",  # we did not look — no soak was passed
)

#: Only a soak row whose EFFECTIVE mode is `apply` mutated the money DB. An
#: `annotate` row records what the pass WOULD have done and changed nothing, so
#: counting it would manufacture contamination that never happened.
SOAK_APPLIED_MODE = "apply"


def soak_applied(soak_rows) -> dict:
    """{trade_id: {"passes": n, "cumulative_qty": q}} over APPLIED soak rows.

    The cumulative sum is why the soak matters beyond corroboration: the journal
    stamp is OVERWRITTEN each pass, so `notes` can only ever yield the LAST
    slice. Summing the soak gives the real total over the window it covers.
    """
    out: dict = {}
    for r in soak_rows or ():
        if str(r.get("mode") or "") != SOAK_APPLIED_MODE:
            continue
        tid = r.get("trade_id")
        if tid is None:
            continue
        try:
            q = abs(float(r.get("attributed_qty")))
        except (TypeError, ValueError):
            q = None
        e = out.setdefault(tid, {"passes": 0, "cumulative_qty": 0.0,
                                 "qty_readable": True})
        e["passes"] += 1
        if q is None:
            # `None`, never a silent 0.0 — an unreadable slice makes the SUM
            # unknown, and a sum that quietly omits it reads as complete.
            e["qty_readable"] = False
        else:
            e["cumulative_qty"] += q
    for e in out.values():
        if not e["qty_readable"]:
            e["cumulative_qty"] = None
    return out


def corroboration(row: dict, applied: dict | None) -> str:
    if applied is None:
        return "soak_not_supplied"
    stamped = qty_basis(row) == "assigned_by_attribution"
    in_soak = row.get("id") in applied
    if stamped and in_soak:
        return "stamp_and_soak"
    if stamped:
        return "stamp_only"
    if in_soak:
        return "soak_only"
    return "neither"


def parse_notes(row: dict):
    """(obj_or_None, readable). `readable` is False for absent/unparseable."""
    v = row.get("notes")
    if v is None or v == "":
        return None, False
    if isinstance(v, dict):
        return v, True
    try:
        obj = json.loads(v)
    except (TypeError, ValueError):
        return None, False
    return (obj, True) if isinstance(obj, dict) else (None, False)


def qty_basis(row: dict) -> str:
    notes, readable = parse_notes(row)
    if not readable:
        return "notes_unreadable"
    if any(k in notes for k in ATTRIBUTION_KEYS):
        return "assigned_by_attribution"
    if notes.get("_truncated"):
        return "evidence_may_be_shed"
    return "observed"


def visibility(row: dict) -> str:
    basis = qty_basis(row)
    if basis == "assigned_by_attribution":
        return ("visible_via_exit_reason"
                if str(row.get("exit_reason") or "") == SCOPED_EXIT_REASON
                else "invisible_to_exit_reason_filter")
    if basis == "observed":
        return "not_attributed"
    return "cannot_grade"


def last_take(row: dict):
    """The LAST attributed slice, or None.

    ⚠️ NOT the cumulative reduction. `_netting_apply_close` OVERWRITES
    `netting_attributed_qty` on every pass, so a row reduced twice retains only
    the second figure. Every quantity derived from it is a LOWER BOUND, and the
    report says so rather than presenting it as a measurement.
    """
    notes, readable = parse_notes(row)
    if not readable:
        return None
    v = notes.get("netting_attributed_qty")
    try:
        return abs(float(v))
    except (TypeError, ValueError):
        return None


def removed_share(row: dict):
    """take / (take + surviving position_size), or None.

    `None`, never 0.0, when either term is missing: a share of zero is a real
    reading (nothing was removed) and must not stand in for *we do not know*.
    """
    take = last_take(row)
    if take is None:
        return None
    try:
        surviving = abs(float(row.get("position_size")))
    except (TypeError, ValueError):
        return None
    denom = take + surviving
    return (take / denom) if denom > 0 else None


#: The two ways this module can fail to obtain a provenance verdict. They are
#: SEPARATE because they have different remedies and different meanings: one
#: says the canonical module is not on the path (an environment fact), the other
#: says it RAISED on a real row (a defect in it or in the row). Collapsing them
#: into one sentinel is the exact class this file exists to measure, and
#: silent-empty-guard caught it here before it shipped.
PROVENANCE_UNIMPORTABLE = "provenance_unimportable"
PROVENANCE_RAISED_PREFIX = "provenance_raised:"


def pnl_provenance(row: dict) -> str:
    """`classify_pnl`'s bucket for this row, from the CANONICAL module.

    Imported rather than re-derived — this file's whole point is a comparison
    against that module's verdict, and a second classifier would make the
    comparison meaningless.

    On failure it returns a NAMED sentinel, never a bucket and never one shared
    value: `provenance_unimportable` when the module is not on the path, and
    `provenance_raised:<ExcType>` when it is there and threw. Neither is ever
    counted as a provenance verdict by `report`.
    """
    try:
        from src.runtime import provenance as P
    except ImportError:
        return PROVENANCE_UNIMPORTABLE
    notes, readable = parse_notes(row)
    merged = dict(row)
    if readable:
        for k in ("pnl_source", "exit_price_source"):
            if k in notes:
                merged[k] = notes[k]
    try:
        bucket, _why = P.classify_pnl(merged)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        # NARROWED to what a classifier can raise on a malformed row. Anything
        # else is a genuine defect and PROPAGATES — a research audit that
        # swallows an unknown error reports a clean population it did not read.
        return f"{PROVENANCE_RAISED_PREFIX}{type(exc).__name__}"
    return str(bucket)


def report(rows: list[dict], account_prefix: str = "bybit",
           soak_rows=None) -> dict:
    """The audit. `account_scope` is ALWAYS stated.

    Only netting venues are graded: attribution runs on Bybit alone, so grading
    an Alpaca row `observed` would be a pass it never had the opportunity to
    fail. The off-venue rows are COUNTED, never silently dropped.
    """
    graded, off_venue = [], 0
    for r in rows:
        if str(r.get("account_id") or "").startswith(account_prefix):
            graded.append(r)
        else:
            off_venue += 1

    applied = soak_applied(soak_rows) if soak_rows is not None else None
    basis_census: collections.Counter = collections.Counter()
    vis_census: collections.Counter = collections.Counter()
    corr_census: collections.Counter = collections.Counter()
    invisible: list[dict] = []
    disagreements: list[dict] = []
    shed: list[dict] = []
    for r in graded:
        b, v = qty_basis(r), visibility(r)
        basis_census[b] += 1
        vis_census[v] += 1
        c = corroboration(r, applied)
        corr_census[c] += 1
        if c == "soak_only":
            # The soak APPLIED a reduction and the journal carries no trace.
            # These are invisible to the exit_reason filter AND to the stamp
            # census, so they are listed in their own right rather than folded
            # into either — a row nobody can see from the journal is a
            # different fact from one an exit_reason filter happens to miss.
            e = applied[r["id"]]
            surviving = None
            try:
                surviving = abs(float(r.get("position_size")))
            except (TypeError, ValueError):
                pass
            cum = e["cumulative_qty"]
            share = (cum / (cum + surviving)
                     if (cum is not None and surviving is not None
                         and cum + surviving > 0) else None)
            shed.append({
                "id": r.get("id"), "account_id": r.get("account_id"),
                "symbol": r.get("symbol"), "strategy_name": r.get("strategy_name"),
                "exit_reason": r.get("exit_reason"), "status": r.get("status"),
                "surviving_position_size": r.get("position_size"),
                "soak_cumulative_qty": cum, "soak_passes": e["passes"],
                "removed_share": share, "pnl": r.get("pnl"),
                "pnl_provenance": pnl_provenance(r),
            })
        if v == "invisible_to_exit_reason_filter":
            invisible.append({
                "id": r.get("id"), "account_id": r.get("account_id"),
                "symbol": r.get("symbol"), "strategy_name": r.get("strategy_name"),
                "exit_reason": r.get("exit_reason"), "status": r.get("status"),
                "surviving_position_size": r.get("position_size"),
                "last_attributed_qty": last_take(r),
                "removed_share_lower_bound": removed_share(r),
                "pnl": r.get("pnl"),
                "pnl_provenance": pnl_provenance(r),
            })
        if b == "assigned_by_attribution" and pnl_provenance(r) == "measured":
            disagreements.append({
                "id": r.get("id"), "exit_reason": r.get("exit_reason"),
                "pnl_provenance": "measured",
                "last_attributed_qty": last_take(r),
                "surviving_position_size": r.get("position_size"),
            })

    scoped = sum(1 for r in graded
                 if str(r.get("exit_reason") or "") == SCOPED_EXIT_REASON)
    attributed = basis_census["assigned_by_attribution"]
    return {
        "account_scope": f"{account_prefix}*",
        "rows_graded": len(graded),
        "rows_off_venue_not_graded": off_venue,
        "qty_basis": dict(basis_census),
        "visibility": dict(vis_census),
        # The two populations the prior work conflated, side by side.
        "rows_matching_exit_reason_filter": scoped,
        "rows_carrying_an_attribution_stamp": attributed,
        "rows_the_exit_reason_filter_misses": len(invisible),
        # `None`, never 0, when nothing is gradeable — the module cannot claim
        # a coverage figure over a population it did not read.
        "ungradeable_rows": (basis_census["evidence_may_be_shed"]
                             + basis_census["notes_unreadable"]),
        "invisible_rows": invisible,
        # `None`, never {} or 0 — no soak supplied means WE DID NOT LOOK, and a
        # zero would read as "the soak agrees there is nothing".
        "corroboration": dict(corr_census) if applied is not None else None,
        "soak_applied_trade_ids": len(applied) if applied is not None else None,
        "rows_whose_stamp_was_SHED": shed if applied is not None else None,
        # The honest total: what the journal alone can see, plus what only the
        # soak can. Reported as ONE number nowhere else, because the two halves
        # have different evidence and a reader must be able to separate them.
        "invisible_total_two_source": (
            (len(invisible) + len(shed)) if applied is not None else None),
        "measured_pnl_on_an_assigned_quantity": disagreements,
        "by_exit_reason_among_attributed": dict(collections.Counter(
            str(r.get("exit_reason") or "") for r in graded
            if qty_basis(r) == "assigned_by_attribution")),
        "by_strategy_among_invisible": dict(collections.Counter(
            str(r.get("strategy_name") or "") for r in graded
            if visibility(r) == "invisible_to_exit_reason_filter")),
        "caveats": {
            "last_attributed_qty_is_the_LAST_slice_not_the_cumulative": True,
            "removed_share_is_a_LOWER_BOUND": True,
            "attribution_keys_are_not_in_DEFAULT_PROTECTED": True,
            "no_close_is_claimed_false_here": True,
        },
    }


def render(v: dict) -> str:
    L = [f"netting-position-size-basis: scope={v['account_scope']} "
         f"graded={v['rows_graded']} (off-venue, not graded: "
         f"{v['rows_off_venue_not_graded']})",
         f"  qty_basis:  {v['qty_basis']}",
         f"  visibility: {v['visibility']}",
         ""]
    L.append("  THE TWO POPULATIONS, SIDE BY SIDE — this is the finding:")
    L.append(f"    rows matching exit_reason='{SCOPED_EXIT_REASON}' (what the "
             f"prior work scoped): {v['rows_matching_exit_reason_filter']}")
    L.append(f"    rows carrying an attribution stamp in notes:              "
             f"{v['rows_carrying_an_attribution_stamp']}")
    L.append(f"    rows the exit_reason filter MISSES:                       "
             f"{v['rows_the_exit_reason_filter_misses']}")
    L.append(f"    rows that could not be graded either way:                 "
             f"{v['ungradeable_rows']}  <- we could not look")
    L.append("")
    if v["invisible_rows"]:
        L.append("  INVISIBLE ROWS — quantity rewritten, labelled as an ordinary exit:")
        for r in v["invisible_rows"]:
            share = r["removed_share_lower_bound"]
            s = "n/a" if share is None else f"{share * 100:.1f}%"
            L.append(f"    trade {r['id']:>6}  {r['account_id']}/{r['symbol']:<9} "
                     f"{str(r['strategy_name']):<20} exit={r['exit_reason']:<14} "
                     f"took {r['last_attributed_qty']} left "
                     f"{r['surviving_position_size']} (>={s} removed) "
                     f"pnl={r['pnl']} provenance={r['pnl_provenance']}")
        L.append("")
    if v["measured_pnl_on_an_assigned_quantity"]:
        n = len(v["measured_pnl_on_an_assigned_quantity"])
        L.append(f"  ⚠️ {n} row(s) are graded `measured` by "
                 f"src.runtime.provenance.classify_pnl WHILE their quantity was")
        L.append("     assigned by attribution. That module grades the PRICE and has "
                 "no quantity axis;")
        L.append("     PnL is price x quantity, so `measured` here is a statement "
                 "about one of the two terms.")
        L.append("")
    if v["corroboration"] is not None:
        L.append(f"  TWO-SOURCE GRADING (soak supplied): {v['corroboration']}")
        L.append(f"    soak rows with mode=apply, distinct trades: "
                 f"{v['soak_applied_trade_ids']}")
        if v["rows_whose_stamp_was_SHED"]:
            L.append("")
            L.append("  ⚠️ STAMP SHED — the soak APPLIED a reduction and the "
                     "journal carries NO trace:")
            for r in v["rows_whose_stamp_was_SHED"]:
                sh = r["removed_share"]
                t = "n/a" if sh is None else f"{sh * 100:.1f}%"
                L.append(f"    trade {r['id']:>6}  {r['account_id']}/"
                         f"{str(r['symbol']):<9} {str(r['strategy_name']):<20} "
                         f"exit={str(r['exit_reason']):<18} took "
                         f"{r['soak_cumulative_qty']} left "
                         f"{r['surviving_position_size']} ({t} removed) "
                         f"pnl={r['pnl']} provenance={r['pnl_provenance']}")
        L.append("")
        L.append(f"  INVISIBLE, BOTH SOURCES COMBINED: "
                 f"{v['invisible_total_two_source']} "
                 f"({len(v['invisible_rows'])} stamped-but-mislabelled + "
                 f"{len(v['rows_whose_stamp_was_SHED'])} stamp-shed). "
                 f"The journal alone sees {len(v['invisible_rows'])}.")
        L.append("")
    L += ["  ⚠️ CAVEATS, none of them optional:",
          "     · `netting_attributed_qty` is OVERWRITTEN each pass, so every "
          "removed-share is a LOWER BOUND.",
          "     · The attribution keys are NOT in json_notes._DEFAULT_PROTECTED, "
          "so a row over the",
          "       500-char cap can lose them. `evidence_may_be_shed` is that "
          "population and it is",
          "       NOT a clean negative.",
          "     · NO CLOSE IS CLAIMED FALSE HERE. That needs the venue-side read "
          "OI-20260908 specifies."]
    if v["corroboration"] is not None:
        L.append("     · THE SOAK IS ITSELF A TAIL. /api/diag/log_file caps its "
                 "read, so the applied set is a")
        L.append("       LOWER BOUND over the window the tail covers — a row "
                 "reduced before it is not absent,")
        L.append("       it is unread.")
    else:
        L.append("     · NO SOAK SUPPLIED, so `evidence_may_be_shed` could not "
                 "be graded at all. Pass --soak;")
        L.append("       the journal alone CANNOT distinguish a shed stamp from "
                 "an unattributed row.")
    return "\n".join(L)


def _self_test() -> int:
    fails: list[str] = []

    def ok(c, label):
        if not c:
            fails.append(label)
        print(f"  {'ok ' if c else 'FAIL'} {label}")

    def row(rid, notes, exit_reason="sl_cross", size=1.0, acct="bybit_1", **kw):
        d = {"id": rid, "account_id": acct, "symbol": "ETHUSDT",
             "strategy_name": "ict_scalp_eth_15m", "exit_reason": exit_reason,
             "status": "closed", "position_size": size, "pnl": 1.0,
             "notes": None if notes is None else json.dumps(notes)}
        d.update(kw)
        return d

    STAMP = {"netting_attribution_basis": "leg_gone", "netting_attributed_qty": 52.83}

    # --- the four basis states, and the one that must not collapse ----------
    ok(qty_basis(row(1, STAMP)) == "assigned_by_attribution",
       "a stamped row reads assigned_by_attribution")
    ok(qty_basis(row(2, {"confidence": 0.7})) == "observed",
       "an unstamped, untruncated row reads observed")
    ok(qty_basis(row(3, {"confidence": 0.7, "_truncated": True}))
       == "evidence_may_be_shed",
       "a TRUNCATED unstamped row is `we could not look`, NEVER observed — the "
       "cap can shed exactly these keys")
    ok(qty_basis(row(4, None)) == "notes_unreadable"
       and qty_basis({"id": 5, "notes": "{not json"}) == "notes_unreadable",
       "absent and unparseable notes are notes_unreadable, distinct from observed")
    ok(qty_basis({"id": 6, "notes": "[1,2,3]"}) == "notes_unreadable",
       "…and a JSON ARRAY is unreadable too, not an empty object")

    # --- EITHER key alone is enough, because the cap sheds one at a time ----
    ok(qty_basis(row(7, {"netting_attribution_basis": "fifo"}))
       == "assigned_by_attribution"
       and qty_basis(row(8, {"netting_attributed_qty": 1.0}))
       == "assigned_by_attribution",
       "either stamp key alone marks the row — dump_capped sheds ONE key at a time")
    ok(qty_basis(row(9, {"netting_attribution_basis": "fifo", "_truncated": True}))
       == "assigned_by_attribution",
       "…and a stamp that SURVIVED truncation still counts as evidence")

    # --- visibility: the finding --------------------------------------------
    ok(visibility(row(10, STAMP, exit_reason="netting_attributed"))
       == "visible_via_exit_reason",
       "a stamped row labelled netting_attributed is visible to the prior filter")
    ok(visibility(row(11, STAMP, exit_reason="sl_cross"))
       == "invisible_to_exit_reason_filter",
       "…and one labelled sl_cross is INVISIBLE to it — the population this unit "
       "exists to name")
    ok(visibility(row(12, {"c": 1})) == "not_attributed"
       and visibility(row(13, {"c": 1, "_truncated": True})) == "cannot_grade",
       "…and an ungradeable row is `cannot_grade`, never folded into not_attributed")

    # --- the lower bound is a lower bound -----------------------------------
    ok(last_take(row(14, STAMP)) == 52.83, "the last slice is read from notes")
    ok(last_take(row(15, {"netting_attributed_qty": "x"})) is None
       and last_take(row(16, {"c": 1})) is None,
       "…and an unreadable or absent slice is None, never 0.0")
    s = removed_share(row(17, {"netting_attributed_qty": 3.0}, size=1.0))
    ok(abs(s - 0.75) < 1e-12, "removed_share is take/(take+surviving)")
    ok(removed_share(row(18, {"c": 1}, size=1.0)) is None,
       "…and is None when there is no slice — a share of 0 is a real reading")
    ok(removed_share(row(19, {"netting_attributed_qty": 0.0}, size=0.0)) is None,
       "…and None rather than a ZeroDivisionError on an all-zero row")

    # --- the report: the two populations must not be conflated --------------
    v = report([row(20, STAMP, exit_reason="netting_attributed"),
                row(21, STAMP, exit_reason="sl_cross"),
                row(22, {"c": 1}),
                row(23, {"c": 1, "_truncated": True}),
                row(24, STAMP, acct="alpaca_live")])
    ok(v["rows_graded"] == 4 and v["rows_off_venue_not_graded"] == 1,
       "a non-netting venue is COUNTED and not graded — attribution never ran there")
    ok(v["rows_matching_exit_reason_filter"] == 1
       and v["rows_carrying_an_attribution_stamp"] == 2,
       "the exit_reason filter and the stamp census are reported SEPARATELY")
    ok(v["rows_the_exit_reason_filter_misses"] == 1
       and v["invisible_rows"][0]["id"] == 21,
       "…and the row the filter misses is NAMED, not just counted")
    ok(v["ungradeable_rows"] == 1,
       "…and the ungradeable population is reported beside them, not inside them")
    ok(v["by_strategy_among_invisible"] == {"ict_scalp_eth_15m": 1},
       "the invisible rows are attributed to a strategy, so a reader knows which "
       "analysis arm inherits them")

    # --- the provenance comparison ------------------------------------------
    m = row(25, dict(STAMP, exit_price_source="exchange"), exit_reason="sl_cross")
    got = pnl_provenance(m)
    ok(got == "measured",
       f"pnl_provenance reads the CANONICAL module and grades this row measured "
       f"(got {got!r})")
    ok(pnl_provenance({"id": 26, "notes": None, "pnl_source": "local_compute"})
       not in (PROVENANCE_UNIMPORTABLE,),
       "…and the module IS importable here, so a failure sentinel would be a "
       "real finding rather than an environment quirk")
    ok(PROVENANCE_UNIMPORTABLE != PROVENANCE_RAISED_PREFIX,
       "…and the two failure sentinels are DISTINCT — 'not on the path' and "
       "'it threw' have different remedies")
    if got == "measured":
        v = report([m])
        ok(len(v["measured_pnl_on_an_assigned_quantity"]) == 1,
           "a row graded `measured` on an ASSIGNED quantity is surfaced — "
           "provenance grades the price and has no quantity axis")
        ok("has no quantity axis" in render(v),
           "…and render says so rather than leaving the reader to notice")
    else:
        ok(False, "provenance module could not be imported — the comparison is "
                  "UNTESTED here rather than passing vacuously")

    # --- the SOAK: the second source, and the one that rescues a shed stamp -
    def sk(tid, qty, mode="apply"):
        return {"trade_id": tid, "attributed_qty": qty, "mode": mode}

    ok(corroboration(row(30, STAMP), None) == "soak_not_supplied",
       "with no soak the corroboration is `we did not look`, never `neither`")
    ok(report([row(31, STAMP)])["corroboration"] is None
       and report([row(31, STAMP)])["invisible_total_two_source"] is None,
       "…and the report returns None there, never {} or 0 — a zero would read as "
       "'the soak agrees there is nothing'")

    #     the finding: a shed stamp is rescued by the soak alone.
    shed_row = row(32, {"c": 1, "_truncated": True}, exit_reason="sl", size=0.06)
    v = report([shed_row], soak_rows=[sk(32, 12.52)])
    ok(v["corroboration"] == {"soak_only": 1},
       "a row the soak APPLIED whose journal stamp is gone grades soak_only")
    ok(len(v["rows_whose_stamp_was_SHED"]) == 1
       and v["rows_whose_stamp_was_SHED"][0]["id"] == 32,
       "…and is NAMED in its own list, not folded into the stamped invisibles")
    ok(v["invisible_total_two_source"] == 1 and len(v["invisible_rows"]) == 0,
       "…and the two-source total counts it while the journal-only list does NOT "
       "— the two have different evidence and must stay separable")
    ok("STAMP SHED" in render(v) and "INVISIBLE, BOTH SOURCES COMBINED" in render(v),
       "…and render reports both numbers rather than one merged figure")
    sh = v["rows_whose_stamp_was_SHED"][0]["removed_share"]
    ok(abs(sh - 12.52 / 12.58) < 1e-12,
       "…and its removed_share uses the soak's CUMULATIVE qty, not a last slice")

    #     an ANNOTATE soak row changed nothing and must not count.
    v = report([shed_row], soak_rows=[sk(32, 12.52, mode="annotate")])
    ok(v["corroboration"] == {"neither": 1} and not v["rows_whose_stamp_was_SHED"],
       "an `annotate` soak row mutated no DB and is NOT counted as contamination")

    #     the cumulative sum is the point, and an unreadable slice voids it.
    a = soak_applied([sk(40, 1.0), sk(40, 2.0), sk(40, 4.0)])
    ok(a[40]["passes"] == 3 and abs(a[40]["cumulative_qty"] - 7.0) < 1e-12,
       "the soak SUMS multiple passes — the journal stamp can only hold the last")
    a = soak_applied([sk(41, 1.0), sk(41, "x")])
    ok(a[41]["passes"] == 2 and a[41]["cumulative_qty"] is None,
       "…and one unreadable slice makes the SUM None, never a total that quietly "
       "omits it")
    ok(soak_applied([{"attributed_qty": 1.0, "mode": "apply"}]) == {},
       "…and a soak row with no trade_id is dropped, never keyed on None")

    #     both sources agreeing is its own state, not a duplicate count.
    v = report([row(33, STAMP, exit_reason="netting_attributed")],
               soak_rows=[sk(33, 1.0)])
    ok(v["corroboration"] == {"stamp_and_soak": 1}
       and v["invisible_total_two_source"] == 0,
       "a row both sources see and the filter can see is invisible to nobody")
    v = report([row(34, STAMP, exit_reason="netting_attributed")], soak_rows=[])
    ok(v["corroboration"] == {"stamp_only": 1},
       "…and a stamped row the soak tail does not reach is stamp_only, NOT a "
       "contradiction — the tail is short, which is a fact about the read")

    # --- an empty population reports nothing rather than a clean bill -------
    v = report([])
    ok(v["rows_graded"] == 0 and v["rows_the_exit_reason_filter_misses"] == 0
       and v["invisible_rows"] == [],
       "an empty population reports zeros with rows_graded=0 beside them, so a "
       "reader cannot mistake it for a clean sweep")
    ok("graded=0" in render(v),
       "…and render leads with the denominator")

    print(f"self-test: {'PASS' if not fails else 'FAIL'} ({len(fails)} failure(s))")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal", help="a saved /api/diag/journal?table=trades payload")
    ap.add_argument("--account-prefix", default="bybit",
                    help="which venue's rows to grade (default: bybit — the only "
                         "one the attribution path runs on)")
    ap.add_argument("--soak", help="a saved /api/diag/log_file?name="
                    "netting_attribution_soak payload. WITHOUT it the journal "
                    "cannot distinguish a shed stamp from an unattributed row, "
                    "and the report says so rather than reporting a clean count")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.journal:
        print("::error::--journal is required: save /api/diag/journal?table=trades&limit=1000 "
              "to a file and pass it. This script does NOT fetch, so it cannot report a "
              "verdict over a population it failed to read.", file=sys.stderr)
        return 2
    rows = json.loads(pathlib.Path(a.journal).read_text())
    if isinstance(rows, dict):
        rows = rows.get("rows") or rows.get("items") or []
    soak_rows = None
    if a.soak:
        blob = json.loads(pathlib.Path(a.soak).read_text())
        raw = blob.get("lines", blob) if isinstance(blob, dict) else blob
        soak_rows = []
        for line in raw:
            if isinstance(line, dict):
                soak_rows.append(line)
                continue
            try:
                obj = json.loads(line)
            except (TypeError, ValueError):
                continue          # a torn tail line is SKIPPED, never guessed
            if isinstance(obj, dict):
                soak_rows.append(obj)
    v = report(rows, account_prefix=a.account_prefix, soak_rows=soak_rows)
    print(json.dumps(v, indent=2) if a.json else render(v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
