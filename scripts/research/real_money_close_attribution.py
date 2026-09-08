#!/usr/bin/env python3
# wiring: manual-only - this is an ANALYST'S instrument, run by a session that is
# asking "did the strategy's exit fire, and over what population?". Giving it a
# scheduled runner would produce a recurring artifact nobody asked a question of,
# which is the written-and-never-read defect this repo already tracks in several
# places. It reads only public Tier-1 GET routes, writes nothing, and mutates no
# state, so there is no post-state for a cron to keep fresh.
"""Attribute every REAL-MONEY closed trade to the mechanism that ACTUALLY closed it.

WHY THIS EXISTS
---------------
``/api/bot/trades/closed`` publishes a ``closeReason`` normalised by
``trades_closed._normalise_close_reason``, which maps ``tp``/``sl``/``manual``
to themselves, ``reconciler*`` to ``reconciler``, and **everything else** to
``other``.  ``other`` therefore contains, side by side:

* genuine STRATEGY exits  — ``vwap_cross``, ``sl_cross``, ``tp_cross``,
  ``exit_head``, ``time_decay``, ``stale_stop``
* things that are NOT a strategy exit — ``operator_flatten_reconciled``,
  ``manual_closeall``, ``backfill_closed_pnl_recovery``, ``netting_attributed``,
  ``adopted_orphan_disappeared``, ``intent_reduce*``, ``stuck_strategy_watchdog``

So ``closeReason`` cannot answer "did the strategy's own exit fire?", and reading
``other`` as "not a strategy exit" is wrong in BOTH directions.  This script
reads the RAW ``trades.exit_reason`` column instead and states its population.

It is the ``UNPROVENANCED DIAGNOSTIC OUTPUT`` sub-class A remedy applied to a
reader rather than a producer: branch on the actual condition, do not reword the
label.  Sibling of ``scripts/research/exit_census.py``.

POPULATIONS — three, never collapsed, because the number moves between them:

  1. LIST        every row /api/bot/trades/closed returns (not_paper +
                 exclude_superseded only).  It is a LIST, not a KPI set.
  2. DECISION    (1) minus the canonical KPI exclusions in
                 ``src/web/api/_clean_trades.py`` (reduce legs, reconciler
                 pseudo-strategies, superseded, reset-flat).
  3. CONVICTABLE (2) restricted to bucket (a) AND ``pnlProvenance == measured``.
                 Only these rows can convict a leg.

⚠️ ``include_paper=true`` DOES NOT WIDEN THE REAL-MONEY POPULATION — it narrows
it.  The default already excludes paper, so ``?limit=200`` returns 200 REAL-MONEY
rows, while ``?limit=200&include_paper=true`` returns the most recent 200 rows of
ALL classes, of which only a handful are real money.  Filtering the latter to
``accountClass=='real_money'`` yields a WINDOW, not a population.  A session did
exactly that on 2026-09-08 and reported n=15 as the real-money population when it
was 447.

Usage (no bearer needed — both routes it reads are Tier-1 unauthenticated):
    python3 scripts/research/real_money_close_attribution.py
    python3 scripts/research/real_money_close_attribution.py --self-test
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from typing import Any

DEFAULT_BASE = "https://ict-bot.duckdns.org"
PAGE = 200

# (a) the STRATEGY's own exit decision fired.
STRATEGY_EXIT: dict[str, str] = {
    "sl": "broker stop fired at the declared level",
    "tp": "broker take-profit fired at the declared level",
    "sl_cross": "monitor observed price cross the declared stop and closed",
    "tp_cross": "monitor observed price cross the declared target and closed",
    "vwap_cross": "the vwap strategy's own exit rule",
    "exit_head": "ML exit head decision",
    "time_decay": "strategy time-based exit",
    "stale_stop": "stale-stop exit-management rule",
}

# (b) something OTHER than a strategy exit booked the row.
NOT_STRATEGY_EXIT: dict[str, tuple[str, str]] = {
    "reconciler_filled": ("reconciler", "reconciler booked a close from exchange state"),
    "reconciler_incomplete": ("reconciler", "reconciler close, incomplete"),
    "exchange_flat_reconciled": ("reconciler", "position read flat on the exchange snapshot"),
    "backfill_closed_pnl_recovery": ("backfill", "an offline backfill process booked the close"),
    "netting_attributed": ("netting", "netting partial-close attribution reconciler"),
    "netting_phantom_reconciled": ("netting", "netting phantom reconciled"),
    "netted_misattributed": ("netting", "netting misattribution"),
    "adopted_orphan_disappeared": ("orphan", "an adopted orphan vanished from the exchange snapshot"),
    "operator_flatten_reconciled": ("operator", "OPERATOR FLATTEN — not a strategy exit at all"),
    "manual_closeall": ("operator", "operator close-all"),
    "intent_reduce_executed": ("bookkeeping", "intent-layer reduce leg (not an independent decision)"),
    "intent_reduce": ("bookkeeping", "intent-layer reduce leg (not an independent decision)"),
    "stuck_strategy_watchdog": ("watchdog", "stuck-strategy watchdog forced the close"),
    "exit_coverage_no_strategy": ("infrastructure", "exit-coverage close with no owning strategy"),
    "pairs_half_open_cleanup": ("infrastructure", "pairs sleeve half-open cleanup"),
    "exchange_reset_flat": ("infrastructure", "wholesale account reset"),
}


def bucket_for(exit_reason: Any) -> tuple[str, str, str]:
    """Three states, never collapsed. ``unknown_mechanism`` is *we did not look* —
    an unrecognised reason must NEVER be silently folded into either real bucket."""
    er = (exit_reason or "").strip()
    if er in STRATEGY_EXIT:
        return "a_strategy_exit", er, STRATEGY_EXIT[er]
    if er in NOT_STRATEGY_EXIT:
        fam, why = NOT_STRATEGY_EXIT[er]
        return "b_not_strategy_exit", er, f"[{fam}] {why}"
    return "unknown_mechanism", er or "(NULL)", "UNRECOGNISED exit_reason — we did not look"


def kpi_exclusions(raw: dict) -> list[str]:
    """The canonical KPI exclusions from src/web/api/_clean_trades.py, which
    /api/bot/trades/closed deliberately does NOT apply (it is a transparent list)."""
    out = []
    if (raw.get("strategy_name") or "") == "orphan_adopt":
        out.append("reconciler_pseudo_strategy")
    if (raw.get("reconcile_status") or "") == "superseded":
        out.append("superseded")
    if (raw.get("exit_reason") or "") == "exchange_reset_flat":
        out.append("reset_flat")
    if (raw.get("setup_type") or "") == "intent_reduce":
        out.append("reduce_leg")
    return out


def run_of_non_wins(graded: list[dict]) -> int:
    n = 0
    for r in reversed(graded):
        if (r.get("realizedPnl") or 0) > 0:
            break
        n += 1
    return n


def p_some_run(p_nonwin: float, n: int, L: int) -> tuple[float, float]:
    """E[# runs >= L] and P(at least one), under an IID model. Stated as IID
    explicitly because these trades are NOT iid — same leg, same symbol, same
    regime — so this is a FLOOR on how ordinary the run is, not a p-value."""
    if n <= L or not (0 < p_nonwin < 1):
        return 0.0, 0.0
    exp_runs = (n - L) * (p_nonwin ** L) * (1 - p_nonwin) + p_nonwin ** L
    return exp_runs, 1 - math.exp(-exp_runs)


def _get(url: str) -> Any:
    # No bearer: BOTH routes this script reads (/api/bot/trades/closed and
    # /api/bot/db/table/trades) are Tier-1 unauthenticated per
    # docs/api-tier-policy.md, verified by an unauthenticated 200 on 2026-09-08.
    # An earlier draft took a `token` argument and never sent it, which
    # diagnostic-provenance-guard correctly flagged as D/inert-parameter.
    with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as fh:
        return json.loads(fh.read().decode())


def fetch(base: str) -> tuple[list[dict], dict[str, dict]]:
    """Page the FULL real-money closed list, then join the raw journal rows.

    ⚠️ ``include_paper`` is deliberately NOT sent — the default already excludes
    paper, and sending it would return a WINDOW rather than the population.
    """
    wire: list[dict] = []
    off = 0
    while True:
        page = _get(f"{base}/api/bot/trades/closed?limit={PAGE}&offset={off}")
        wire.extend(page)
        if len(page) < PAGE:
            break
        off += PAGE
        if off > 20000:
            raise RuntimeError("refusing to page past 20000 — bounded read")
    ids = {r["id"] for r in wire}
    if len(ids) != len(wire):
        raise RuntimeError(f"duplicate ids across pages: {len(wire)} rows, {len(ids)} unique")

    raw: dict[str, dict] = {}
    for acct in sorted({r["account"] for r in wire}):
        off = 0
        while True:
            d = _get(
                f"{base}/api/bot/db/table/trades?limit=500&offset={off}"
                f"&filter_col=account_id&filter_op=eq&filter_val={acct}"
            )
            # ⚠️ An unknown filter column is IGNORED, not an error — `total` would
            # then be the WHOLE table. Assert before trusting anything.
            #
            # collapsed-state: applied — every other state is REFUSED alike, and
            # that is the point rather than a collapse. `not_requested`,
            # `ignored_unknown_column` and `ignored_bad_op` differ in WHY the
            # filter did not form a WHERE, but they are identical in what they
            # license here: nothing. A read that is not account-scoped would join
            # the whole `trades` table onto a real-money analysis, so this is a
            # REFUSAL, never a fallback. The distinction is not thrown away — the
            # raised message carries the verbatim state, so a caller sees which
            # one occurred and can act on it; what is deliberately not offered is
            # a code path that proceeds on any of them.
            # collapsed-state: order_state — NOT CONSULTED, deliberately: this
            # function sends no `order_by`, so ordering cannot have been dropped.
            # Page order is irrelevant here because every page is accumulated and
            # the join is by id, and id-uniqueness is asserted against the row
            # count rather than assumed from an ordering.
            if d.get("filter_state") != "applied":
                raise RuntimeError(f"filter_state={d.get('filter_state')!r} for {acct} — refusing to trust")
            for r in d["rows"]:
                raw[str(r["id"])] = r
            if len(d["rows"]) < 500:
                break
            off += 500
    missing = [r["id"] for r in wire if r["id"] not in raw]
    if missing:
        raise RuntimeError(f"{len(missing)} wire rows have no journal row: {missing[:5]}")
    return wire, raw


def analyse(wire: list[dict], raw: dict[str, dict], out=sys.stdout) -> dict:
    for w in wire:
        w["_raw"] = raw[w["id"]]
        w["_bucket"], w["_mech"], w["_why"] = bucket_for(w["_raw"].get("exit_reason"))
        w["_excl"] = kpi_exclusions(w["_raw"])

    def p(*a):
        print(*a, file=out)

    kept = [w for w in wire if not w["_excl"]]
    dropped = [w for w in wire if w["_excl"]]
    assert len(kept) + len(dropped) == len(wire), "arithmetic cross-check FAILED"

    p("POPULATION 1 — LIST: n=%d | accounts %s | %s .. %s"
      % (len(wire), sorted({w["account"] for w in wire}),
         min(w["closedAt"] for w in wire)[:10], max(w["closedAt"] for w in wire)[:10]))
    p("POPULATION 2 — DECISION SET: kept=%d dropped=%d %s"
      % (len(kept), len(dropped), dict(Counter(tuple(w["_excl"]) for w in dropped))))

    b = Counter(w["_bucket"] for w in kept)
    p("\nMECHANISM over the decision set:")
    for k in ("a_strategy_exit", "b_not_strategy_exit", "unknown_mechanism"):
        p("  %-22s %4d  %5.1f%%" % (k, b[k], 100 * b[k] / len(kept) if kept else 0))
    for buck in ("a_strategy_exit", "b_not_strategy_exit", "unknown_mechanism"):
        sub = [w for w in kept if w["_bucket"] == buck]
        if sub:
            p(" %s: %s" % (buck, dict(Counter(w["_mech"] for w in sub).most_common())))

    graded = [w for w in kept if w.get("realizedPnl") is not None]
    wins = [w for w in graded if w["realizedPnl"] > 0]
    losses = [w for w in graded if w["realizedPnl"] < 0]
    flat = [w for w in graded if w["realizedPnl"] == 0]
    graded.sort(key=lambda w: w["closedAt"])
    L = run_of_non_wins(graded)
    p_nw = (len(losses) + len(flat)) / len(graded) if graded else 0.0
    exp_runs, pany = p_some_run(p_nw, len(graded), L)
    p("\nLIFETIME (decision set, pnl NOT NULL) n=%d: W=%d L=%d flat=%d sum=%.2f win%%(excl flat)=%.1f"
      % (len(graded), len(wins), len(losses), len(flat),
         sum(w["realizedPnl"] for w in graded),
         100 * len(wins) / (len(wins) + len(losses)) if (wins or losses) else 0))
    p("TAIL RUN of consecutive non-wins: %d" % L)
    p("  IID FLOOR: p_nonwin=%.4f  E[runs>=%d in %d]=%.3f  P(some such run)~%.1f%%"
      % (p_nw, L, len(graded), exp_runs, 100 * pany))
    p("  ^ these trades are NOT iid; this is a floor on how ordinary the run is, not a p-value.")

    conv = [w for w in kept if w["_bucket"] == "a_strategy_exit"
            and w.get("realizedPnl") is not None and w.get("pnlProvenance") == "measured"]
    p("\nPOPULATION 3 — CONVICTABLE (a + measured): n=%d" % len(conv))
    by = defaultdict(list)
    for w in conv:
        by[(w["pattern"], w["account"])].append(w)
    for (lg, acct), rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        tot = sum(r["realizedPnl"] for r in rs)
        p("  %-24s %-13s n=%-4d W=%-3d L=%-3d sum=%9.2f"
          % (lg, acct, len(rs), sum(1 for r in rs if r["realizedPnl"] > 0),
             sum(1 for r in rs if r["realizedPnl"] < 0), tot))
    return {"list": len(wire), "decision": len(kept), "convictable": len(conv),
            "tail_run": L, "by_leg": {f"{k[0]}@{k[1]}": len(v) for k, v in by.items()}}


def self_test() -> int:
    ok = 0

    def chk(cond, msg):
        nonlocal ok
        assert cond, msg
        ok += 1

    chk(bucket_for("sl")[0] == "a_strategy_exit", "sl is a strategy exit")
    chk(bucket_for("vwap_cross")[0] == "a_strategy_exit", "vwap_cross is a strategy exit")
    chk(bucket_for("sl_cross")[0] == "a_strategy_exit", "sl_cross is a strategy exit")
    chk(bucket_for("tp_cross")[0] == "a_strategy_exit", "tp_cross is a strategy exit")
    chk(bucket_for("operator_flatten_reconciled")[0] == "b_not_strategy_exit", "operator flatten is not")
    chk(bucket_for("reconciler_filled")[0] == "b_not_strategy_exit", "reconciler is not")
    chk(bucket_for("backfill_closed_pnl_recovery")[0] == "b_not_strategy_exit", "backfill is not")
    # the state that must exist: we did not look
    chk(bucket_for("some_new_reason_nobody_declared")[0] == "unknown_mechanism", "unknown stays unknown")
    chk(bucket_for(None)[0] == "unknown_mechanism", "NULL stays unknown")
    chk(bucket_for("")[0] == "unknown_mechanism", "empty stays unknown")
    # NON-VACUITY: the three buckets must be genuinely reachable and disjoint
    chk(len(set(STRATEGY_EXIT) & set(NOT_STRATEGY_EXIT)) == 0, "taxonomies are disjoint")
    # KPI exclusions
    chk(kpi_exclusions({"setup_type": "intent_reduce"}) == ["reduce_leg"], "reduce leg excluded")
    chk(kpi_exclusions({"strategy_name": "orphan_adopt"}) == ["reconciler_pseudo_strategy"], "orphan excluded")
    chk(kpi_exclusions({"reconcile_status": "superseded"}) == ["superseded"], "superseded excluded")
    chk(kpi_exclusions({"strategy_name": "vwap"}) == [], "an ordinary row is not excluded")
    # run detection
    rows = [{"realizedPnl": 1.0}, {"realizedPnl": -1.0}, {"realizedPnl": -2.0}]
    chk(run_of_non_wins(rows) == 2, "tail run counts back to the last win")
    chk(run_of_non_wins([{"realizedPnl": 5.0}]) == 0, "a winning tail is a run of 0")
    chk(run_of_non_wins([{"realizedPnl": 0.0}]) == 1, "a FLAT row is a non-win, not a win")
    # the iid floor must behave
    e1, p1 = p_some_run(0.72, 424, 14)
    chk(0 < p1 < 1 and e1 > 0, "iid floor is a probability")
    chk(p_some_run(0.72, 5, 14) == (0.0, 0.0), "n <= L yields no estimate rather than a fake one")
    print(f"self-test OK — {ok} assertions")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.environ.get("BOT_API_BASE", DEFAULT_BASE))
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit the summary as JSON")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    wire, raw = fetch(a.base)
    summary = analyse(wire, raw)
    if a.json:
        print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
