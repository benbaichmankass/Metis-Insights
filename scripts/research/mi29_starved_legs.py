#!/usr/bin/env python3
# wiring: manual-only — a MEASUREMENT of live fleet state, answering the operator
# decision DEC-20260910-MI-29-MEASURE-THE-STARVED-LEGS-FIRST once. It reads the live
# VM through the diag relay, which CI cannot reach and which a cadence would hammer;
# re-run it by hand when the arbitration allowlist, the roster, or an execution gate
# changes — the memo's own resolution criteria say which.
"""MI-29 — which legs are starved, how often, and does the fan-out cover them.

THE OPERATOR'S QUESTION, VERBATIM
--------------------------------
``DEC-20260910-MI-29-MEASURE-THE-STARVED-LEGS-FIRST``, chosen *"Route it — measure
which legs, how often, and bring the options back"*. Its stated scope:

    measurement only — which legs, how many ticks they lost a contest, and whether
    the per-account arbitration fan-out (armed on bybit_1 since 2026-08-31) already
    covers some of them. NO Tier-3 disposition is proposed off the existing
    evidence: the instrument has not been re-measured since the fan-out was armed,
    so some 'never traded' legs may now be trading.

THE ONE DISTINCTION THIS WHOLE UNIT TURNS ON
--------------------------------------------
``src/runtime/arbitration_fanout.py`` already states it and already paid for it:

    ⚠️ ``starved`` MEANS *ANOTHER ACCOUNT TOOK THE WINNER FROM ME*, AND NOTHING
    WIDER. A tick on which NO strategy won the symbol at all is graded ``no_winner``
    … Until 2026-08-30 those ticks were graded ``starved``: on the whole live file
    that was 11 of the 13 starved gradings, **overstating the finding 6.5×** in the
    sole evidence base for the Tier-3 change. Do not re-merge the two.

This module does not re-merge them, and it does not invent a third conflation of its
own either: a leg that is a candidate while its account elects SOMEBODY ELSE is
``lost_to_sibling``; a leg that is a candidate while its account elects NOBODY is
``no_winner``. Both look like "lost a contest" to a naive tally and only the first
is one. (The first draft of this measurement reported `trend_donchian` losing 64% of
its contests. Every one of those was ``no_winner`` — it lost to nobody.)

THREE POPULATIONS, NEVER POOLED, AND THE REASON IS MECHANICAL
------------------------------------------------------------
``arbitration_fanout_soak.record`` writes a row only when the tick is NOTABLE:

    notable = bool(plan.get("apply_rounds")) or starved or no_winner or
              winner_unattributed

Before 2026-09-12, ``apply_rounds`` was a two-key projection its own reader refused,
so it was **always empty** and only a starved/no-winner/unattributed account could
put a row on disk. After the repair it is populated on every planned tick, so quiet
ticks now appear. **The inclusion rule therefore changed under the same filename**,
and a rate computed across the whole file divides post-repair numerators by a
pre-repair denominator. Rows are split on ``fanout_schema`` (2 vs 3) for that reason
and for no other.

A FILL IS NOT A ROW IN ``trades``
---------------------------------
This repo journals REFUSALS into ``trades`` (``status='rejected'``), and orphan
adoptions too (``setup_type='adopted_orphan'``). Measured here: of 999 in-window
rows, **365 are refusals and 27 are adoptions**. Counting rows per leg reports
``mgc_trend_1h`` — the leg MI-29 filed as having ZERO fills — as having 51 trades,
of which 50 are its own refusals and 1 is an adopted orphan. A fill is a row that is
neither.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

REFUSED_STATUSES = ("rejected", "exchange_rejected")
ADOPTED = "adopted_orphan"

#: Per-account outcomes for a leg that WAS a candidate, never collapsed.
LOST_TO_SIBLING = "lost_to_sibling"      # a DIFFERENT named leg was elected
NO_WINNER = "no_winner"                  # nothing was elected — NOT starvation
ELECTED = "elected"
OUTCOMES = (ELECTED, LOST_TO_SIBLING, NO_WINNER)

#: Execution-gate states. `NOT_IN_CONFIG` is *we could not look*, never `live`.
EXEC_NOT_IN_CONFIG = "NOT_IN_CONFIG"
EXEC_DISABLED = "disabled"


def diag(path: str) -> Any:
    """Fetch a diag payload through the repo's own resolver.

    Raises rather than returning a default: an unreachable VM is *we could not
    look*, and a census over an empty fetch would read as a clean fleet.
    """
    out = subprocess.run(
        ["bash", str(_REPO_ROOT / "scripts" / "ops" / "diag_fetch.sh"), path],
        capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise SystemExit(f"diag_fetch failed rc={out.returncode} for {path}: {out.stderr[-400:]}")
    # `diag_fetch.sh` writes its `served by <base>` provenance line to STDERR, so
    # stdout is the document alone. It is parsed WHOLE rather than trimmed to the
    # last brace: an earlier draft did that and silently truncated every JSON ARRAY
    # payload (the journal tables), which surfaced as a decode error four million
    # characters in rather than as a wrong answer — this time.
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"diag_fetch returned non-JSON for {path} "
            f"({len(out.stdout)} bytes): {exc}") from exc


def execution_gate(leg: str, strategies: Dict[str, Any]) -> str:
    v = strategies.get(leg)
    if not isinstance(v, dict):
        return EXEC_NOT_IN_CONFIG
    if not v.get("enabled", True):
        return EXEC_DISABLED
    return str(v.get("execution") or "live")


def classify_candidate(leg: str, elected: Optional[str]) -> str:
    """What happened to this candidate on this account this tick.

    ⚠️ ``elected is None`` is ``no_winner`` and NOT a loss. That is the 6.5×
    overstatement `arbitration_fanout` already paid for, one level up.
    """
    if elected == leg:
        return ELECTED
    return NO_WINNER if elected is None else LOST_TO_SIBLING


def is_fill(row: Dict[str, Any]) -> bool:
    """A genuine strategy entry — not a journalled refusal, not an adoption."""
    return (row.get("status") not in REFUSED_STATUSES
            and row.get("setup_type") != ADOPTED)


def census_soak(rows: List[Dict[str, Any]], strategies: Dict[str, Any]) -> Dict[str, Any]:
    cand = collections.Counter()
    by_outcome: Dict[str, collections.Counter] = {o: collections.Counter() for o in OUTCOMES}
    acct_state = collections.Counter()
    starved_holding = collections.Counter()
    starved_by_account = collections.Counter()
    winner_that_took = collections.Counter()
    for r in rows:
        elected_by = r.get("elected_by_account") or {}
        for acct, info in (r.get("per_account") or {}).items():
            state = info.get("state")
            acct_state[f"{acct}:{state}"] += 1
            if state == "starved":
                starved_by_account[acct] += 1
                winner_that_took[r.get("winning_strategy")] += 1
                for c in (info.get("candidates") or []):
                    starved_holding[c] += 1
            win = elected_by.get(acct)
            for c in (info.get("candidates") or []):
                cand[c] += 1
                by_outcome[classify_candidate(c, win)][c] += 1
    legs = sorted(cand, key=lambda x: -by_outcome[LOST_TO_SIBLING].get(x, 0))
    return {
        "rows": len(rows),
        "account_state_counts": dict(acct_state.most_common()),
        "per_leg": [
            {"leg": leg, "execution": execution_gate(leg, strategies),
             "candidate": cand[leg],
             "elected": by_outcome[ELECTED].get(leg, 0),
             "lost_to_sibling": by_outcome[LOST_TO_SIBLING].get(leg, 0),
             "no_winner": by_outcome[NO_WINNER].get(leg, 0)}
            for leg in legs],
        "starved_by_account": dict(starved_by_account.most_common()),
        "starved_candidate_held": [
            {"leg": leg, "execution": execution_gate(leg, strategies), "count": n}
            for leg, n in starved_holding.most_common()],
        "winner_that_took_the_symbol": dict(winner_that_took.most_common()),
        "totals": {
            "lost_to_sibling": sum(by_outcome[LOST_TO_SIBLING].values()),
            "no_winner": sum(by_outcome[NO_WINNER].values()),
            "elected": sum(by_outcome[ELECTED].values()),
        },
    }


def overlap_window(*tables: List[Dict[str, Any]]) -> Tuple[str, str]:
    """The span in which EVERY table is complete under its own 1000-row cap.

    Each diag table is capped independently, so a busy table reaches back days and a
    quiet one reaches back months. Comparing a leg's packages against its fills
    across mismatched spans counts packages whose fills fall outside the fill table —
    MI-29's own text calls this out and this function is why it cannot recur here.
    """
    los, his = [], []
    for t in tables:
        stamps = [r["created_at"] for r in t if r.get("created_at")]
        if not stamps:
            raise SystemExit("overlap_window: a table carries no created_at — refusing to guess")
        los.append(min(stamps))
        his.append(max(stamps))
    return max(los), min(his)


def census_journal(packages: List[Dict[str, Any]], trades: List[Dict[str, Any]],
                   strategies: Dict[str, Any]) -> Dict[str, Any]:
    lo, hi = overlap_window(packages, trades)
    pk = [r for r in packages if r.get("created_at") and lo <= r["created_at"] <= hi]
    tr = [r for r in trades if r.get("created_at") and lo <= r["created_at"] <= hi]
    pkc = collections.Counter(r.get("strategy_name") for r in pk)
    rows_all = collections.Counter(r.get("strategy_name") for r in tr)
    fills = collections.Counter(r.get("strategy_name") for r in tr if is_fill(r))
    refusals = collections.Counter(r.get("strategy_name") for r in tr
                                   if r.get("status") in REFUSED_STATUSES)
    live = sorted(leg for leg in strategies
                  if execution_gate(leg, strategies) == "live")
    zero_fill = [
        {"leg": leg, "packages": pkc.get(leg, 0), "trade_rows": rows_all.get(leg, 0),
         "of_which_refusals": refusals.get(leg, 0), "fills": 0}
        for leg in live if fills.get(leg, 0) == 0]
    return {
        "window": {"from": lo, "to": hi,
                   "packages_in_window": len(pk), "trades_in_window": len(tr)},
        "row_kinds": {
            "refusals": sum(1 for r in tr if r.get("status") in REFUSED_STATUSES),
            "adoptions": sum(1 for r in tr if r.get("setup_type") == ADOPTED),
            "fills": sum(1 for r in tr if is_fill(r)),
            "total": len(tr)},
        "live_legs": len(live),
        "zero_fill_live_legs": zero_fill,
    }


def census_dispatch(packages: List[Dict[str, Any]],
                    soak: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Has the fan-out dispatched, and has it ever dispatched a NON-global-winner?

    The second question is the one that matters: a fan-out that only ever dispatches
    the leg the global election would have picked anyway is exercised and has changed
    nothing. They are different facts and a single "it dispatched" would hide it.
    """
    hits = []
    for r in packages:
        m = r.get("meta")
        if isinstance(m, str):
            try:
                m = json.loads(m)
            except ValueError:
                m = None
        if isinstance(m, dict) and "arbitration_fanout_round" in m:
            hits.append((r, m))
    idx: Dict[Tuple[Any, Any, Any], List[Tuple[dict, dict]]] = collections.defaultdict(list)
    for s in soak:
        for rd in (s.get("rounds_applied") or []):
            idx[(s.get("symbol"), rd.get("strategy"),
                 round(float(rd.get("entry") or 0), 8))].append((s, rd))
    differed = same = unmatched = 0
    geom_ok = geom_mismatch_amended = geom_mismatch_unexplained = 0
    for r, m in hits:
        key = (r.get("symbol"), r.get("strategy_name"), round(float(r.get("entry") or 0), 8))
        cands = idx.get(key) or []
        if not cands:
            unmatched += 1
            continue
        # ⚠️ ACROSS EVERY matching soak row, not the first: the same
        # (symbol, strategy, entry) recurs across ticks, and taking cands[0]
        # silently answers about a different tick.
        winners = {s.get("winning_strategy") for s, _ in cands}
        if winners - {r.get("strategy_name")}:
            differed += 1
        else:
            same += 1
        if any(abs(float(rd["sl"]) - float(r["sl"])) <= 1e-6
               and abs(float(rd["tp"]) - float(r["tp"])) <= 1e-6 for _, rd in cands):
            geom_ok += 1
        elif str(r.get("updated_at")) != str(r.get("created_at")):
            # the package was AMENDED after dispatch (a trailing stop), so its row
            # carries the CURRENT geometry while the soak carries the DISPATCHED
            # one. Explained, and not a divergence.
            geom_mismatch_amended += 1
        else:
            geom_mismatch_unexplained += 1
    stamps = sorted(r.get("created_at") for r, _ in hits if r.get("created_at"))
    accts = collections.Counter()
    for _, m in hits:
        for a in (m["arbitration_fanout_round"].get("accounts") or []):
            accts[a] += 1
    return {
        "packages_carrying_a_fanout_round": len(hits),
        "packages_scanned": len(packages),
        "span": {"from": stamps[0] if stamps else None, "to": stamps[-1] if stamps else None},
        "by_account": dict(accts.most_common()),
        "by_strategy": dict(collections.Counter(
            r.get("strategy_name") for r, _ in hits).most_common()),
        "by_status": dict(collections.Counter(r.get("status") for r, _ in hits).most_common()),
        "dispatched_leg_equals_global_winner": same,
        "dispatched_leg_differs_from_global_winner": differed,
        "unjoinable_to_a_soak_round": unmatched,
        "geometry_matches_a_soak_round": geom_ok,
        "geometry_mismatch_explained_by_a_later_amend": geom_mismatch_amended,
        "geometry_mismatch_UNEXPLAINED": geom_mismatch_unexplained,
    }


def _selftest() -> int:
    checks: List[Tuple[str, bool]] = []

    def ck(n: str, c: bool) -> None:
        checks.append((n, bool(c)))

    S = {"a": {"enabled": True, "execution": "live"},
         "b": {"enabled": True, "execution": "shadow"},
         "c": {"enabled": False, "execution": "live"}}
    ck("exec: live", execution_gate("a", S) == "live")
    ck("exec: shadow", execution_gate("b", S) == "shadow")
    ck("exec: disabled beats execution", execution_gate("c", S) == EXEC_DISABLED)
    # NEG1 — an unknown leg is 'we could not look', never defaulted to live
    ck("NEG1: unknown leg is NOT_IN_CONFIG, not live",
       execution_gate("zzz", S) == EXEC_NOT_IN_CONFIG)

    ck("classify: elected", classify_candidate("a", "a") == ELECTED)
    ck("classify: lost to sibling", classify_candidate("a", "b") == LOST_TO_SIBLING)
    # NEG2 — the 6.5x overstatement: no winner must NEVER read as a loss
    ck("NEG2: elected=None is no_winner, NOT lost_to_sibling",
       classify_candidate("a", None) == NO_WINNER)
    ck("states distinct", len(set(OUTCOMES)) == 3)

    ck("fill: a closed strategy row is a fill",
       is_fill({"status": "closed", "setup_type": "a"}))
    # NEG3 — a journalled refusal is not a fill
    ck("NEG3: rejected is not a fill",
       not is_fill({"status": "rejected", "setup_type": "a"}))
    ck("NEG3b: exchange_rejected is not a fill",
       not is_fill({"status": "exchange_rejected", "setup_type": "a"}))
    # NEG4 — an adopted orphan is not a fill even when closed
    ck("NEG4: adopted_orphan is not a fill",
       not is_fill({"status": "closed", "setup_type": ADOPTED}))

    a = [{"created_at": "2026-01-01"}, {"created_at": "2026-06-01"}]
    b = [{"created_at": "2026-03-01"}, {"created_at": "2026-05-01"}]
    ck("overlap window is the intersection", overlap_window(a, b) == ("2026-03-01", "2026-05-01"))
    # NEG5 — a table with no timestamps must refuse, never widen the window
    try:
        overlap_window(a, [{"x": 1}])
        ck("NEG5: undateable table refuses", False)
    except SystemExit:
        ck("NEG5: undateable table refuses", True)

    soak = [{"symbol": "S", "winning_strategy": "a",
             "elected_by_account": {"acc1": "a", "acc2": None},
             "per_account": {"acc1": {"state": "routed", "candidates": ["a", "b"]},
                             "acc2": {"state": "no_winner", "candidates": ["b"]}}}]
    c = census_soak(soak, S)
    per = {r["leg"]: r for r in c["per_leg"]}
    ck("census: a elected once", per["a"]["elected"] == 1)
    ck("census: b lost to a sibling once", per["b"]["lost_to_sibling"] == 1)
    ck("census: b also has one no_winner", per["b"]["no_winner"] == 1)
    # NEG6 — the two must not be summed into one 'lost' figure
    ck("NEG6: totals keep the two apart",
       c["totals"]["lost_to_sibling"] == 1 and c["totals"]["no_winner"] == 1)

    starved = [{"symbol": "S", "winning_strategy": "a",
                "elected_by_account": {"acc": "a"},
                "per_account": {"acc": {"state": "starved", "candidates": ["b"]}}}]
    cs = census_soak(starved, S)
    ck("census: starved account counted", cs["starved_by_account"] == {"acc": 1})
    ck("census: the starved account's held candidate named",
       cs["starved_candidate_held"][0]["leg"] == "b")

    pkgs = [{"symbol": "S", "strategy_name": "a", "entry": 1.0, "sl": 0.9, "tp": 1.2,
             "created_at": "t", "updated_at": "t", "status": "closed",
             "meta": {"arbitration_fanout_round": {"strategy": "a", "accounts": ["acc"]}}}]
    sk = [{"symbol": "S", "winning_strategy": "a",
           "rounds_applied": [{"strategy": "a", "entry": 1.0, "sl": 0.9, "tp": 1.2}]}]
    d = census_dispatch(pkgs, sk)
    ck("dispatch: the round is found", d["packages_carrying_a_fanout_round"] == 1)
    ck("dispatch: equals the global winner", d["dispatched_leg_equals_global_winner"] == 1)
    ck("dispatch: geometry matches", d["geometry_matches_a_soak_round"] == 1)
    # NEG7 — a different global winner must be reported, not smoothed away
    sk2 = [{"symbol": "S", "winning_strategy": "zzz",
            "rounds_applied": [{"strategy": "a", "entry": 1.0, "sl": 0.9, "tp": 1.2}]}]
    ck("NEG7: a non-global-winner dispatch is counted",
       census_dispatch(pkgs, sk2)["dispatched_leg_differs_from_global_winner"] == 1)
    # NEG8 — an amended row explains a geometry mismatch; an unamended one does not
    pk2 = [dict(pkgs[0], sl=0.5, updated_at="LATER")]
    pk3 = [dict(pkgs[0], sl=0.5)]
    ck("NEG8: amend explains a mismatch",
       census_dispatch(pk2, sk)["geometry_mismatch_explained_by_a_later_amend"] == 1)
    ck("NEG8b: an unamended mismatch stays UNEXPLAINED",
       census_dispatch(pk3, sk)["geometry_mismatch_UNEXPLAINED"] == 1)

    ok = sum(1 for _, c_ in checks if c_)
    for n, c_ in checks:
        if not c_:
            print(f"  FAIL  {n}")
    print(f"mi29 selftest: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--out", default="docs/research/mi29-starved-legs-2026-09-18.json")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.run:
        ap.print_help()
        return 2

    import yaml
    cfg = yaml.safe_load((_REPO_ROOT / "config" / "strategies.yaml").read_text())
    strategies = cfg.get("strategies", cfg)

    soak_payload = diag("/api/diag/log_file?name=arbitration_fanout_soak&lines=4000")
    soak = [json.loads(x) for x in soak_payload["lines"]]
    packages = diag("/api/diag/journal?table=order_packages&limit=1000")
    trades = diag("/api/diag/journal?table=trades&limit=1000")

    by_schema = collections.Counter(r.get("fanout_schema") for r in soak)
    s2 = [r for r in soak if r.get("fanout_schema") == 2]
    s3 = [r for r in soak if r.get("fanout_schema") == 3]
    stamps = sorted(r.get("logged_at_utc") for r in soak if r.get("logged_at_utc"))
    payload = {
        "unit": "MI-29",
        "answers": "DEC-20260910-MI-29-MEASURE-THE-STARVED-LEGS-FIRST",
        "generated_at": "2026-09-18",
        "soak_population": {
            "rows_returned": len(soak),
            "file_size_bytes": soak_payload.get("size_bytes"),
            "complete_file": len(soak) < 1000,
            "span": {"from": stamps[0] if stamps else None,
                     "to": stamps[-1] if stamps else None},
            "by_schema": {str(k): v for k, v in by_schema.items()},
        },
        "schema_2_pre_repair": census_soak(s2, strategies),
        "schema_3_post_repair": census_soak(s3, strategies),
        "journal": census_journal(packages, trades, strategies),
        "fanout_dispatch": census_dispatch(packages, soak),
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
