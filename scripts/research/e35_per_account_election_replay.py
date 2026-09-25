#!/usr/bin/env python3
# wiring: manual-only — a one-shot MEASUREMENT for checklist row E35, replayed
# over the live `arbitration_fanout_soak` log. It reads the live VM through the
# diag surface, which CI cannot reach; re-run it by hand after the E35 deploy
# (the v4 rows then carry `global_would_drop` directly).
"""E35 — per account, which order decisions the OLD routing dropped and the NEW routes.

WHAT IS REPLAYED, AND WHY THIS IS EXACT RATHER THAN A SIMULATION
----------------------------------------------------------------
Every soak row since 2026-08-31 carries ``rounds_planned``: the output of
``arbitration_fanout.plan_per_account_election`` over that tick's ONCE-gated
candidate set. E35 does not change that function's choice — it makes its
output the dispatch. So ``rounds_planned`` IS the new routing decision for the
tick, read off the live process, not re-derived here.

Three routings are compared per ``(row, account)``:

* ``global``  — the election underneath everything (BL-20260827): only the
  accounts holding the one global winner (``winner_accounts``) are routed.
* ``armed``   — what the fan-out as deployed actually handed the dispatcher:
  ``rounds_applied`` on a v3 row when non-empty, else the global path. A v2 row's
  ``rounds_applied`` is COSMETIC (93/93 dispatched nothing — see
  ``arbitration_fanout.FANOUT_SCHEMA``), so a v2 row is graded as ``global``.
* ``new``     — ``rounds_planned``: every account that elected a winner of its
  own is routed that winner.

⚠️ THE POPULATION IS THE SOAK'S, NOT EVERY TICK. The writer drops a tick on which
every candidate-holding account held the global winner. On such a tick all three
routings coincide (an account holding the global winner elects it from its own
subset — same sort key), so nothing that differs is lost. Say "over N soak rows",
never "over N ticks".

⚠️ THIS IS THE ROUTING DECISION, NOT A FILL. The per-strategy gates (open
package, bar debounce, refusal cooldown, empty-sizing) and the account's own
RiskManager still run after it. An account counted here as "new routes" would
have had a package built for it; whether that package filled is a journal read.

Usage::

    bash scripts/ops/diag_fetch.sh \
        '/api/diag/log_file?name=arbitration_fanout_soak&lines=20000' > soak.json
    python3 scripts/research/e35_per_account_election_replay.py soak.json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from typing import Any, Dict, Iterable, List, Optional

OUTCOMES = (
    "same",               # old and new route the same strategy
    "new_only",           # old dropped the account, new routes it   <- the fix
    "old_only",           # old routed, new does not                 <- MUST be 0 (P1)
    "changed",            # both route, different strategies
    "neither",            # the account had candidates, nothing elected
)


def _account_strategy(rounds: Iterable[Any], account: str) -> Optional[str]:
    hits = [
        str(r.get("strategy"))
        for r in rounds or ()
        if isinstance(r, dict) and account in (r.get("accounts") or ())
    ]
    if len(hits) > 1:
        # An account in two rounds would be a double-place. Surface it loudly
        # rather than pick one.
        return "DOUBLE:" + "+".join(sorted(hits))
    return hits[0] if hits else None


def routes_for(row: Dict[str, Any], account: str) -> Dict[str, Optional[str]]:
    winner = row.get("winning_strategy")
    global_ = winner if account in (row.get("winner_accounts") or ()) else None
    applied = row.get("rounds_applied") or []
    if row.get("fanout_schema") == 3 and applied:
        armed = _account_strategy(applied, account)
    else:
        armed = global_
    new = _account_strategy(row.get("rounds_planned") or [], account)
    return {"global": global_, "armed": armed, "new": new}


def outcome(old: Optional[str], new: Optional[str]) -> str:
    if old and new:
        return "same" if old == new else "changed"
    if new:
        return "new_only"
    if old:
        return "old_only"
    return "neither"


def replay(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    table: Dict[str, Dict[str, collections.Counter]] = collections.defaultdict(
        lambda: {"global": collections.Counter(), "armed": collections.Counter()}
    )
    doubles = 0
    graded = 0
    for row in rows:
        if row.get("plan_state") != "planned" or row.get("plan_roster_state") != "read":
            continue
        for account, cell in (row.get("per_account") or {}).items():
            if not (cell or {}).get("candidates"):
                continue
            graded += 1
            r = routes_for(row, account)
            if any(str(v or "").startswith("DOUBLE:") for v in r.values()):
                doubles += 1
            for old in ("global", "armed"):
                table[account][old][outcome(r[old], r["new"])] += 1
    return {"table": table, "account_ticks_graded": graded, "double_routes": doubles}


def _load(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    lines = doc["lines"] if isinstance(doc, dict) else doc
    return [json.loads(l) if isinstance(l, str) else l for l in lines]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("soak_json")
    ap.add_argument("--since", default="", help="ISO lower bound on logged_at_utc")
    ap.add_argument("--until", default="", help="ISO upper bound (exclusive)")
    args = ap.parse_args(argv)
    rows = [
        r for r in _load(args.soak_json)
        if (not args.since or str(r.get("logged_at_utc")) >= args.since)
        and (not args.until or str(r.get("logged_at_utc")) < args.until)
    ]
    if not rows:
        print("no rows in window")
        return 1
    ts = sorted(str(r.get("logged_at_utc")) for r in rows)
    res = replay(rows)
    print(f"population: {len(rows)} soak rows, {ts[0]} -> {ts[-1]}; "
          f"{res['account_ticks_graded']} (row, account) gradings with candidates; "
          f"double-routes in any routing: {res['double_routes']}")
    for old in ("global", "armed"):
        print(f"\nOLD = {old}")
        print(f"{'account':18}" + "".join(f"{o:>10}" for o in OUTCOMES))
        tot = collections.Counter()
        for account in sorted(res["table"]):
            c = res["table"][account][old]
            tot.update(c)
            print(f"{account:18}" + "".join(f"{c[o]:>10}" for o in OUTCOMES))
        print(f"{'TOTAL':18}" + "".join(f"{tot[o]:>10}" for o in OUTCOMES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
