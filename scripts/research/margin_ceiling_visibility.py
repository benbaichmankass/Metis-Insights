#!/usr/bin/env python3
# wiring: manual-only — a one-shot audit of a SPECIFIC open claim
#   (PB-20260822) against a journal pull. Scheduling it would re-ask a question
#   over a window nobody chose, and the answer moves only when sizing policy or
#   the stamp's write site changes.
"""MI-278 U37 — was the order size a RISK decision or a CEILING, and can we even tell?

THE CLAIM
---------
`PB-20260822-AVAX-SCALP-SIZED-OFF-MARGIN-NOT-RISK` (open, `high`, Tier-3):
*"`ict_scalp_avax_5m`'s order size IS the margin ceiling, not a risk number —
`position_size` equals `max_qty_by_margin` exactly"*, measured on **n=2**, and
its `next_action` asks for the pre-clamp risk qty to be stamped beside the cap.

⚠️ THREE THINGS THIS MEASURES THAT THE ROW COULD NOT
------------------------------------------------------

**1. THE ROW'S OWN EQUALITY TEST RETURNS ZERO, AND THAT IS A TRAP.** It says
`position_size` equals `max_qty_by_margin` *"to FULL FLOAT PRECISION"*. It does
not: the shipped size is **QUANTISED** to the venue step (33141.1000 against a
cap of 33141.1001). An `==` probe therefore finds **nothing** across the whole
journal and a session would conclude the effect had stopped. This module grades
the **RATIO**, which is what the claim actually means, and reports the band so
no threshold is hidden.

**2. THE RECORD IS REFUSAL-ONLY, SO THE ROW'S CRITERION CANNOT BE CHECKED.**
`notes.margin_basis` is stamped by exactly one writer —
`execute.log_rejection_to_journal`, whose own docstring says it exists to *"log
a refusal event"* — so **a placed order can never carry it**. The row's
criterion requires that *no order **places*** at a ceiling, and its
`why_it_matters` is entirely about the moment a refusal becomes a fill. The
evidence disappears at exactly that transition. `visibility_state` is that
finding, per row.

**3. A SECOND, INDEPENDENT HAZARD ON THE SAME FIELD.** `margin_basis` is a
nested DICT stored in `notes`, which is written through
`json_notes.dump_capped(notes, 500)`, and it is not in `_DEFAULT_PROTECTED`.
`_shrink_dict` trims STRINGS and sheds whole unprotected values it cannot trim —
a dict is exactly that. So even if the stamp were written on the placement path
it would be the first thing dropped on a full row. Reported, not conflated with
(2): one is a write site, the other a cap, and fixing either alone leaves the
other.

WHAT IT DOES NOT DO
-------------------
It changes nothing and proposes no size. `src/units/accounts/risk.py` and
`execute.py` are Tier-2 and are read only. Per-leg sizing is Tier-3.

Usage:
  python3 scripts/research/margin_ceiling_visibility.py --self-test
  python3 scripts/research/margin_ceiling_visibility.py --journal j.json
  python3 scripts/research/margin_ceiling_visibility.py --journal j.json --json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

#: Statuses that mean the order REACHED the venue and became a position. The
#: row's criterion is about these, and they are exactly the rows that can never
#: carry a `margin_basis`.
PLACED_STATUSES = ("closed", "open")

#: Statuses that mean the order did NOT place.
REFUSED_STATUSES = ("rejected", "exchange_rejected")

#: How close the shipped size sat to the margin cap. Bands, not a single
#: threshold, because the row's own `==` test is what returns nothing —
#: publishing the band is what stops the next reader picking a number.
CEILING_BANDS = (
    "at_ceiling",       # ratio >= 0.999 — quantisation is the only difference
    "near_ceiling",     # 0.99 <= ratio < 0.999
    "part_of_ceiling",  # 0.5 <= ratio < 0.99
    "well_below",       # 0 < ratio < 0.5
    "zero_size",        # the sizer returned nothing at all — NOT "below"
    "ungradeable",      # size or cap unreadable / cap <= 0 — we could not look
)

#: Can this row tell us whether its size was ceiling-bound?
VISIBILITY_STATES = (
    "recorded",              # margin_basis is present
    "absent_on_placed",      # THE FINDING — it placed, so no stamp exists
    "absent_on_refused",     # refused and still no stamp (sizing not reached)
    "absent_status_unknown",
)


def parse_notes(row: dict):
    v = row.get("notes")
    if isinstance(v, dict):
        return v
    if not v:
        return None
    try:
        o = json.loads(v)
    except (TypeError, ValueError):
        return None
    return o if isinstance(o, dict) else None


def _f(v):
    try:
        return abs(float(v))
    except (TypeError, ValueError):
        return None


def ceiling_band(row: dict) -> tuple[str, float | None]:
    """(band, ratio). `ratio` is None whenever the band is not a comparison."""
    nt = parse_notes(row) or {}
    mb = nt.get("margin_basis")
    if not isinstance(mb, dict):
        return "ungradeable", None
    ps, cap = _f(row.get("position_size")), _f(mb.get("max_qty_by_margin"))
    if ps is None or cap is None or cap <= 0:
        return "ungradeable", None
    if ps == 0:
        # NOT `well_below`. A zero size is the sizer REFUSING, a different event
        # from a size that came in under the cap, and pooling them would let 154
        # refusals read as 154 conservatively-sized orders.
        return "zero_size", 0.0
    ratio = ps / cap
    if ratio >= 0.999:
        return "at_ceiling", ratio
    if ratio >= 0.99:
        return "near_ceiling", ratio
    if ratio >= 0.5:
        return "part_of_ceiling", ratio
    return "well_below", ratio


def visibility_state(row: dict) -> str:
    nt = parse_notes(row) or {}
    if isinstance(nt.get("margin_basis"), dict):
        return "recorded"
    st = str(row.get("status") or "")
    if st in PLACED_STATUSES:
        return "absent_on_placed"
    if st in REFUSED_STATUSES:
        return "absent_on_refused"
    return "absent_status_unknown"


def basis_provenance(row: dict) -> str | None:
    """Was `basis_usd` a broker reading or a reconstruction? `None` if no stamp.

    The stamp's own `detail` says so — an `equity_minus_pledged` basis carries
    `(ESTIMATED: open notional / 3x)`. A cap computed from an estimated basis is
    an estimated cap, and a row at that cap is at an estimated ceiling.
    """
    mb = (parse_notes(row) or {}).get("margin_basis")
    if not isinstance(mb, dict):
        return None
    return ("estimated" if "ESTIMATED" in str(mb.get("detail") or "")
            else "direct")


def report(rows: list[dict]) -> dict:
    bands: collections.Counter = collections.Counter()
    vis: collections.Counter = collections.Counter()
    kinds: collections.Counter = collections.Counter()
    prov: collections.Counter = collections.Counter()
    at_ceiling_legs: collections.Counter = collections.Counter()
    at_ceiling_accounts: collections.Counter = collections.Counter()
    at_ceiling: list[dict] = []
    exceeded: list[dict] = []
    for r in rows:
        v = visibility_state(r)
        vis[v] += 1
        if v != "recorded":
            continue
        b, ratio = ceiling_band(r)
        bands[b] += 1
        mb = (parse_notes(r) or {})["margin_basis"]
        kinds[str(mb.get("kind"))] += 1
        prov[str(basis_provenance(r))] += 1
        if ratio is not None and ratio > 1.0:
            # The clamp did NOT bind. Reported separately because it is the
            # opposite failure and would be the more serious one.
            exceeded.append({"id": r.get("id"), "ratio": ratio,
                             "strategy": r.get("strategy_name")})
        if b == "at_ceiling":
            at_ceiling_legs[str(r.get("strategy_name"))] += 1
            at_ceiling_accounts[str(r.get("account_id"))] += 1
            at_ceiling.append({
                "id": r.get("id"), "strategy": r.get("strategy_name"),
                "account_id": r.get("account_id"), "status": r.get("status"),
                "position_size": r.get("position_size"),
                "max_qty_by_margin": mb.get("max_qty_by_margin"),
                "ratio": round(ratio, 9) if ratio is not None else None,
                "basis_kind": mb.get("kind"),
                "basis_provenance": basis_provenance(r),
            })
    graded = sum(bands.values())
    return {
        "rows_read": len(rows),
        "visibility": dict(vis),
        "rows_with_a_ceiling_record": graded,
        "bands": dict(bands),
        "basis_kind": dict(kinds),
        "basis_provenance": dict(prov),
        "at_ceiling_by_strategy": dict(at_ceiling_legs),
        "at_ceiling_by_account": dict(at_ceiling_accounts),
        "at_ceiling_rows": at_ceiling,
        # The opposite failure, reported even when empty so its absence is a
        # measured zero rather than a field nobody looked at.
        "clamp_did_not_bind": exceeded,
        # `None`, never 0.0, when nothing was gradeable.
        "at_ceiling_share": (round(bands["at_ceiling"] / graded, 4)
                             if graded else None),
        "placed_rows": sum(1 for r in rows
                           if str(r.get("status") or "") in PLACED_STATUSES),
        "placed_rows_with_a_ceiling_record": sum(
            1 for r in rows if str(r.get("status") or "") in PLACED_STATUSES
            and visibility_state(r) == "recorded"),
        # The positive control for the claim above: if these rows had no notes
        # at all the absence would prove nothing about the stamp.
        "placed_rows_with_any_notes": sum(
            1 for r in rows if str(r.get("status") or "") in PLACED_STATUSES
            and parse_notes(r) is not None),
        "placed_rows_truncated": sum(
            1 for r in rows if str(r.get("status") or "") in PLACED_STATUSES
            and (parse_notes(r) or {}).get("_truncated")),
    }


def render(v: dict) -> str:
    L = [f"margin-ceiling-visibility: {v['rows_read']} journal row(s) read", ""]
    L.append(f"  visibility : {v['visibility']}")
    L.append(f"  bands      : {v['bands']}   (over "
             f"{v['rows_with_a_ceiling_record']} row(s) carrying a record)")
    L.append(f"  basis kind : {v['basis_kind']}")
    L.append(f"  basis prov : {v['basis_provenance']}")
    share = v["at_ceiling_share"]
    L.append(f"  AT CEILING : {v['bands'].get('at_ceiling', 0)} "
             f"({'n/a' if share is None else f'{share:.1%}'} of recorded)  "
             f"by strategy {v['at_ceiling_by_strategy']}  "
             f"by account {v['at_ceiling_by_account']}")
    L.append("")
    L.append("  ⚠️ THE RECORD IS REFUSAL-ONLY — the row's criterion is about "
             "orders that PLACE:")
    L.append(f"     placed rows (closed/open)          : {v['placed_rows']}")
    L.append(f"     …carrying ANY notes dict           : "
             f"{v['placed_rows_with_any_notes']}   <- positive control")
    L.append(f"     …carrying a margin_basis record    : "
             f"{v['placed_rows_with_a_ceiling_record']}")
    L.append(f"     …whose notes were TRUNCATED        : "
             f"{v['placed_rows_truncated']}   <- the second, separate hazard")
    L.append("")
    if v["clamp_did_not_bind"]:
        L.append(f"  🚨 {len(v['clamp_did_not_bind'])} row(s) sized ABOVE their "
                 f"own cap — the clamp did not bind: {v['clamp_did_not_bind'][:5]}")
    else:
        L.append("  clamp_did_not_bind: 0 — every recorded size is at or under "
                 "its cap (a measured zero, not an unchecked field)")
    if v["at_ceiling_rows"]:
        L.append("")
        L.append(f"  {'id':>6} {'strategy':<22} {'account':<17} {'status':<18} "
                 f"{'size':>13} {'cap':>15} {'ratio':>10} {'basis':<20}")
        for r in v["at_ceiling_rows"][:15]:
            L.append(f"  {str(r['id']):>6} {str(r['strategy']):<22} "
                     f"{str(r['account_id']):<17} {str(r['status']):<18} "
                     f"{float(r['position_size']):>13.4f} "
                     f"{float(r['max_qty_by_margin']):>15.4f} "
                     f"{r['ratio']:>10.6f} "
                     f"{str(r['basis_kind']) + '/' + str(r['basis_provenance']):<20}")
    L += ["",
          "  ⚠️ AN EXACT `position_size == max_qty_by_margin` PROBE RETURNS ZERO.",
          "     Sizes are QUANTISED to the venue step, so the ratio is 0.999999x",
          "     and not 1.0. PB-20260822's 'full float precision' wording will",
          "     send the next reader to a clean negative.",
          "  ⚠️ THIS CHANGES NOTHING. risk.py and execute.py are Tier-2 and are",
          "     read only; per-leg sizing is Tier-3."]
    return "\n".join(L)


def _self_test() -> int:
    fails: list[str] = []

    def ok(c, label):
        if not c:
            fails.append(label)
        print(f"  {'ok ' if c else 'FAIL'} {label}")

    def row(rid, size, cap, status="rejected", leg="ict_scalp_avax_5m",
            acct="bybit_1", detail=None, kind="venue_available", stamp=True,
            extra_notes=None):
        nt = dict(extra_notes or {})
        if stamp:
            nt["margin_basis"] = {"kind": kind, "basis_usd": 1000.0,
                                  "leverage": 3, "buffer": 0.9,
                                  "max_qty_by_margin": cap, "detail": detail}
        return {"id": rid, "position_size": size, "status": status,
                "strategy_name": leg, "account_id": acct,
                "notes": json.dumps(nt) if nt else None}

    # --- the band a QUANTISED size lands in, which is the row's whole trap ---
    b, r = ceiling_band(row(1, 33141.1000, 33141.1001))
    ok(b == "at_ceiling" and r < 1.0,
       "a QUANTISED size grades at_ceiling on the ratio while `==` would miss "
       "it — this is the trap PB-20260822's wording sets")
    ok(ceiling_band(row(2, 33141.1001, 33141.1001))[0] == "at_ceiling",
       "…and an exactly-equal size grades the same band, so the fix does not "
       "lose the original case")

    for size, cap, want in ((0.995, 1.0, "near_ceiling"), (0.7, 1.0, "part_of_ceiling"),
                            (0.2, 1.0, "well_below")):
        ok(ceiling_band(row(3, size, cap))[0] == want,
           f"ratio {size / cap} grades {want}")

    ok(ceiling_band(row(4, 0.0, 1.0))[0] == "zero_size",
       "a ZERO size is its own band — the sizer refusing is not a "
       "conservatively-sized order, and pooling them lets refusals read as "
       "prudence")
    ok(ceiling_band(row(5, 1.0, 0.0))[0] == "ungradeable"
       and ceiling_band(row(6, 1.0, 1.0, stamp=False))[0] == "ungradeable"
       and ceiling_band(row(7, "x", 1.0))[0] == "ungradeable",
       "a zero cap, an absent stamp and an unreadable size are all ungradeable "
       "— we could not look, never a band")

    # --- visibility: the finding ---------------------------------------------
    ok(visibility_state(row(10, 1.0, 2.0)) == "recorded",
       "a stamped row is `recorded`")
    ok(visibility_state(row(11, 1.0, 2.0, status="closed", stamp=False))
       == "absent_on_placed",
       "a PLACED row with no stamp is `absent_on_placed` — the row's criterion "
       "is about exactly these and they can never carry the record")
    ok(visibility_state(row(12, 1.0, 2.0, status="rejected", stamp=False))
       == "absent_on_refused",
       "…and a REFUSED row with no stamp is a different state: sizing was not "
       "reached, which is not the same finding")
    ok(visibility_state({"id": 13, "status": "weird", "notes": None})
       == "absent_status_unknown",
       "…and an unrecognised status is its own state, never folded into either")

    # --- the report, and its positive control ---------------------------------
    rows = [row(20, 33141.1, 33141.1001), row(21, 27758.8, 27758.8013),
            row(22, 0.0, 5.0), row(23, 0.6, 1.0),
            row(24, 1.0, 2.0, status="closed", stamp=False,
                extra_notes={"pnl_source": "local_compute"}),
            row(25, 1.0, 2.0, status="open", stamp=False,
                extra_notes={"_truncated": True})]
    v = report(rows)
    ok(v["bands"]["at_ceiling"] == 2 and v["bands"]["zero_size"] == 1
       and v["bands"]["part_of_ceiling"] == 1,
       "the bands partition the recorded rows")
    ok(v["placed_rows"] == 2 and v["placed_rows_with_a_ceiling_record"] == 0,
       "no PLACED row carries a ceiling record")
    ok(v["placed_rows_with_any_notes"] == 2,
       "…and the POSITIVE CONTROL holds: those rows DO have notes, so the "
       "absence is about the stamp and not about missing notes")
    ok(v["placed_rows_truncated"] == 1,
       "…and truncated placed rows are counted as the SECOND, separate hazard")
    ok(v["at_ceiling_by_strategy"] == {"ict_scalp_avax_5m": 2},
       "at-ceiling rows are attributed to a strategy")
    ok(v["at_ceiling_share"] == round(2 / 4, 4),
       "the share is over the RECORDED rows, and its denominator is published")

    # --- the opposite failure is reported even when empty --------------------
    ok(v["clamp_did_not_bind"] == []
       and "measured zero" in render(v),
       "a clamp that always bound reports an EMPTY list and says the zero was "
       "measured, not that the field went unchecked")
    v2 = report([row(30, 3.0, 1.0)])
    ok(len(v2["clamp_did_not_bind"]) == 1 and "🚨" in render(v2),
       "…and a size ABOVE its own cap is surfaced loudly — the opposite and "
       "more serious failure")

    # --- basis provenance -----------------------------------------------------
    ok(basis_provenance(row(40, 1.0, 2.0, kind="equity_minus_pledged",
                            detail="equity 1 less pledged (ESTIMATED: x/3x)"))
       == "estimated",
       "an ESTIMATED basis is flagged — a cap from an estimated basis is an "
       "estimated ceiling")
    ok(basis_provenance(row(41, 1.0, 2.0)) == "direct"
       and basis_provenance(row(42, 1.0, 2.0, stamp=False)) is None,
       "…a direct basis reads direct, and no stamp reads None rather than "
       "either")

    # --- an empty population reports its denominator -------------------------
    v3 = report([])
    ok(v3["rows_read"] == 0 and v3["at_ceiling_share"] is None,
       "an empty population reports share None, never 0.0")
    ok("0 journal row(s) read" in render(v3),
       "…and render leads with the denominator")

    ok("Tier-2" in render(v), "render states that the code paths are Tier-2 "
                              "and read only")

    print(f"self-test: {'PASS' if not fails else 'FAIL'} ({len(fails)} failure(s))")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal", help="a saved /api/diag/journal?table=trades payload")
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
    v = report(rows)
    print(json.dumps(v, indent=2) if a.json else render(v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
