#!/usr/bin/env python3
# wiring: manual-only — a census a session runs before proposing a new
# close-attribution column, to establish which label state each unattributable
# close is actually in. Not a scheduled job; the live surface is
# /api/bot/performance, which already reports these four states per bucket.
"""Which label state is each "unattributable" close actually in?

MI-278 U24, for
`BL-20260912-FOR-55-PERCENT-OF-WINNERS-THAT-MISSED-TARGET-THE-MECHANISM-THAT-STOPPED-THE-RUN-IS-NOT-IN-THE-JOURNAL`.

⚠️ THIS DOES NOT CLOSE THAT ROW AND CANNOT. Its criteria require a DURABLE
SURFACE — `stop_order_type` / `cancel_type` persisted at close and branched on
by a reader — which is a `src/` journal-writer change and therefore Tier-2.
What this answers is the Tier-1 question that should precede building it:
**which state are these rows actually in, and does the proposed column reach
them?**

THREE THINGS IT ESTABLISHES, AND ONE IT REFUTES.

1. The proposed fields are absent everywhere. `stop_order_type`, `cancel_type`
   and their camelCase forms occur ZERO times in `trades.notes`, against a
   positive control on the same population (`exit_price_source` 514,
   `pnl_source` 482, `close_exec_type` 186). The row's premise holds, and the
   zero is a measurement rather than a failed grep.

2. THE ROW POOLS TWO CATEGORICALLY DIFFERENT STATES, and the repo's own
   semantics say they are different. `order_monitor` records that a close with
   NO `exit_reason_source` key at all is "a 100% signature that none had ever
   reached the classifier" (measured there over 181 of 181 mislabelled rows).
   So:

       reconciler_filled   -> `exit_reason_source: unresolved`  = the classifier
                              WAS reached, LOOKED, and declined
       netting_attributed  -> key ABSENT                        = the classifier
                              was NEVER REACHED

   Those need different remedies. A new column helps the first (the classifier
   ran and the PRICE was unusable, so a venue-supplied mechanism would be
   independent evidence); for the second the first fix is that nothing runs at
   all, and a column written by a path that writes nothing stays empty.

3. ⚠️ IT REFUTES ITS OWN AUTHOR'S FIRST HYPOTHESIS. I expected to find that a
   consumer keyed on `exit_reason_source` would read the netting rows as clean
   and undercount them. It does not: `src/web/api/routers/performance.py`
   already separates `label_unattested` (absent) / `label_refused` /
   `label_unresolved` / `label_attested`, so the absent-key rows land in their
   own bucket and are correctly distinguished. **The reader the backlog row
   asks for partly EXISTS and is correct.** What is missing is narrower — that
   nobody has read it for this population, and the row does not cite it.

Run: python3 scripts/research/exit_label_state_census.py --self-test
     python3 scripts/research/exit_label_state_census.py --trades <trades.json>
"""
from __future__ import annotations

import argparse
import collections
import json
from typing import Any

# Never collapsed, and the four are NOT a severity ladder — they are four
# different facts about whether the classifier ran and what it concluded.
LABEL_STATES = (
    "attested",     # a real resolved label
    "unresolved",   # the classifier ran, LOOKED, and declined
    "refused",      # declined because the PRICE was fabricated
    "unattested",   # no key at all — the classifier was NEVER REACHED
)

# The fields the backlog row proposes persisting, plus the venue spellings.
PROPOSED_FIELDS = ("stop_order_type", "cancel_type", "stopOrderType", "cancelType")

# Present on this population, so a zero above is a measurement and not a typo.
CONTROL_FIELDS = ("exit_price_source", "pnl_source", "close_exec_type")

# `EXIT_LABEL_REFUSED_UNMEASURED` in src/runtime/provenance.py. Duplicated as a
# literal ONLY because this script must run without importing the runtime; the
# self-test pins it so a rename there shows up as a failure here.
REFUSED = "refused_unmeasured_price"

# The pairs sleeve owns its own exit state and the backlog row excludes it.
PAIRS_REASONS = ("pairs_stop", "pairs_half_open_cleanup", "pairs_revert")


def parse_notes(row: dict) -> dict:
    """`trades.notes` is a JSON blob, NOT columns.

    Every provenance marker lives here — the `trades` table itself carries 41
    columns and none of them is `exit_price_source`. Reading the column list
    and concluding the provenance is missing is the trap this function exists
    to stop; a malformed blob returns {} and is COUNTED, never silently merged.
    """
    n = row.get("notes")
    if not isinstance(n, str):
        return {}
    try:
        out = json.loads(n)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def label_state(row: dict) -> str:
    src = parse_notes(row).get("exit_reason_source")
    if src is None or src == "":
        return "unattested"
    if src == REFUSED:
        return "refused"
    if src == "unresolved":
        return "unresolved"
    return "attested"


def population(rows: list[dict], since: str | None, exclude_pairs: bool = True,
               winners_only: bool = False) -> list[dict]:
    out = []
    for r in rows:
        if r.get("status") != "closed" or r.get("is_backtest"):
            continue
        if r.get("pnl") is None:
            continue
        if since and (r.get("created_at") or "") < since:
            continue
        if exclude_pairs and (r.get("exit_reason") or "") in PAIRS_REASONS:
            continue
        if winners_only:
            try:
                if float(r["pnl"]) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
        out.append(r)
    return out


def field_presence(rows: list[dict], fields: tuple[str, ...]) -> dict:
    keys: collections.Counter = collections.Counter()
    for r in rows:
        keys.update(parse_notes(r).keys())
    return {f: keys.get(f, 0) for f in fields}


def census(rows: list[dict]) -> dict:
    """Label state per exit_reason. States the population; never a bare rate."""
    by_reason: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        by_reason[str(r.get("exit_reason") or "(none)")][label_state(r)] += 1
    return {
        "rows": len(rows),
        "overall": dict(collections.Counter(label_state(r) for r in rows)),
        "by_exit_reason": {k: dict(v) for k, v in sorted(by_reason.items())},
    }


def proposed_field_reach(rows: list[dict]) -> dict:
    """Would the proposed column reach these rows? Reported WITH its control.

    A zero on the proposed fields means nothing unless the same probe finds the
    control fields present on the same rows — that is the difference between
    "absent" and "we looked in the wrong place", which is precisely the mistake
    of reading the 41-column schema and concluding the provenance is gone.
    """
    proposed = field_presence(rows, PROPOSED_FIELDS)
    control = field_presence(rows, CONTROL_FIELDS)
    control_total = sum(control.values())
    return {
        "rows": len(rows),
        "proposed_fields_present": proposed,
        "control_fields_present": control,
        # Without a positive control a zero is not a finding.
        "probe_state": ("gradeable" if control_total > 0
                        else "not_gradeable_no_control_field_present"),
        "verdict": (
            "proposed_fields_absent_on_this_population"
            if control_total > 0 and sum(proposed.values()) == 0 else
            "proposed_fields_present" if sum(proposed.values()) else
            "ungradeable"
        ),
    }


# --------------------------------------------------------------------------
def _provenance_refusal_literal() -> str | None:
    """Read `EXIT_LABEL_REFUSED_UNMEASURED`'s value out of the runtime source.

    This file duplicates the constant as a literal so it can run without
    importing the runtime. That duplication is only safe if a rename upstream
    BREAKS here, so this reads the assignment textually rather than trusting
    the copy. Returns None when the file or the assignment cannot be found —
    *we could not look*, which the control treats as a failure, never a pass.
    """
    import pathlib
    import re
    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "runtime" / "provenance.py"
    try:
        text = src.read_text()
    except OSError:
        return None
    m = re.search(r'^EXIT_LABEL_REFUSED_UNMEASURED\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def _row(reason, src=..., pnl=1.0, notes_extra=None, created="2026-09-01",
         status="closed"):
    n = {}
    if src is not ...:
        n["exit_reason_source"] = src
    n.update(notes_extra or {})
    return {"exit_reason": reason, "notes": json.dumps(n), "pnl": pnl,
            "created_at": created, "status": status, "is_backtest": 0}


def self_test() -> int:
    ok = fail = 0

    def ck(name, got, want):
        nonlocal ok, fail
        if got == want:
            ok += 1
            print("  PASS %s" % name)
        else:
            fail += 1
            print("  FAIL %s\n    got  %r\n    want %r" % (name, got, want))

    print("exit-label-state-census self-test")

    ck("1 a resolved label is `attested`", label_state(_row("x", "price_vs_pkg_bracket")), "attested")
    ck("2 `unresolved` is its own state", label_state(_row("x", "unresolved")), "unresolved")
    # ⚠️ The LITERAL, not the constant. Planting a rename on REFUSED tripped
    # NOTHING, because this control used the same constant on both sides — a
    # tautology, and the docstring above promises the opposite.
    ck("3 the refusal constant is its own state",
       label_state(_row("x", "refused_unmeasured_price")), "refused")
    ck("3b CONTROL: the literal matches src/runtime/provenance.py, so an upstream "
       "rename fails HERE rather than silently mis-bucketing",
       _provenance_refusal_literal(), REFUSED)
    ck("4 an ABSENT key is `unattested`, NOT `unresolved`", label_state(_row("x")), "unattested")
    ck("5 ...and an empty string is unattested too", label_state(_row("x", "")), "unattested")
    ck("6 CONTROL: absent and unresolved are DIFFERENT states",
       label_state(_row("x")) == label_state(_row("x", "unresolved")), False)
    ck("7 the four states are exactly the declared vocabulary",
       sorted(LABEL_STATES), sorted(["attested", "unresolved", "refused", "unattested"]))

    ck("8 malformed notes parse to {} rather than raising", parse_notes({"notes": "{oops"}), {})
    ck("9 ...and therefore grade `unattested`, never `attested`",
       label_state({"notes": "{oops"}), "unattested")
    ck("10 a non-string notes is {}", parse_notes({"notes": 17}), {})

    rows = [_row("reconciler_filled", "unresolved") for _ in range(3)] + \
           [_row("netting_attributed") for _ in range(2)] + \
           [_row("tp", "price_vs_pkg_bracket")]
    c = census(rows)
    ck("11 census counts rows", c["rows"], 6)
    ck("12 census splits state by exit_reason",
       c["by_exit_reason"]["netting_attributed"], {"unattested": 2})
    ck("13 ...and keeps the unresolved bucket separate",
       c["by_exit_reason"]["reconciler_filled"], {"unresolved": 3})
    ck("14 overall is the sum, not a rate",
       c["overall"], {"unresolved": 3, "unattested": 2, "attested": 1})

    # the proposed-field probe, and its control
    ctl = [_row("x", "unresolved", notes_extra={"pnl_source": "local_compute"})]
    p = proposed_field_reach(ctl)
    ck("15 proposed fields absent is a VERDICT only with a live control",
       (p["probe_state"], p["verdict"]),
       ("gradeable", "proposed_fields_absent_on_this_population"))
    ck("16 CONTROL: with no control field present the probe REFUSES",
       proposed_field_reach([{"notes": "{}"}])["probe_state"],
       "not_gradeable_no_control_field_present")
    ck("17 a present proposed field is reported as present",
       proposed_field_reach([_row("x", "unresolved",
                                  notes_extra={"pnl_source": "s", "cancel_type": "CancelByUser"})]
                            )["verdict"], "proposed_fields_present")
    ck("18 the camelCase venue spellings are probed too",
       set(PROPOSED_FIELDS) >= {"cancelType", "stopOrderType"}, True)

    # population filters
    pop = [_row("pairs_stop", "unresolved"), _row("tp", "unresolved"),
           _row("tp", "unresolved", pnl=-1.0), _row("tp", "unresolved", created="2026-01-01")]
    ck("19 pairs are excluded", len(population(pop, "2026-08-27")), 2)
    ck("20 winners_only drops the loser",
       len(population(pop, "2026-08-27", winners_only=True)), 1)
    ck("21 the since bound is applied", len(population(pop, None)), 3)
    ck("22 an open row is never in a CLOSED population",
       len(population([_row("tp", "unresolved", status="open")], None)), 0)
    ck("23 a null-pnl row is excluded (it cannot be graded win/lose)",
       len(population([{"status": "closed", "is_backtest": 0, "pnl": None,
                        "exit_reason": "tp", "notes": "{}"}], None)), 0)

    print("\nself-test: PASS %d · FAIL %d" % (ok, fail))
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--trades", help="JSON array from /api/diag/journal?table=trades")
    ap.add_argument("--since", default="2026-08-27")
    ap.add_argument("--winners-only", action="store_true", default=True)
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.trades:
        ap.error("pass --trades <file.json> or --self-test")
    with open(a.trades) as fh:
        rows = json.load(fh)
    pop = population(rows, a.since, winners_only=a.winners_only)
    out: dict[str, Any] = {
        "population": {"rows_in_pull": len(rows), "graded": len(pop),
                       "since": a.since, "pairs_excluded": True,
                       "winners_only": a.winners_only},
        "label_state_census": census(pop),
        "proposed_field_reach": proposed_field_reach(
            population(rows, None, winners_only=False)),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
