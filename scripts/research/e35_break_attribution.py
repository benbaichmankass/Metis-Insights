#!/usr/bin/env python3
# wiring: manual — re-run by a session working
# BL-20260911-A-DATED-REGIME-BREAK-ON-2026-08-30-COLLAPSED-THE-DIRECTIONAL-LEGS-WIN-RATE-AND-NOBODY-NOTICED-FOR-TWO-WEEKS
"""Discriminate the 2026-08-30 break: is it the e35 bracket geometry, or the market?

WHY THIS IS A SCRIPT AND NOT A PARAGRAPH
----------------------------------------
The row's criterion asks a session to state *which candidate it established, how,
and over what population*, and warns that **"the market chopped" is not a verdict
unless measured** — it needs the non-e35 control degrading equally. A prose answer
cannot be re-run when n grows, and n is exactly what this question is short of.
So the comparison is code: point it at a fresh journal pull and it re-grades.

THE THREE ARMS, AND THE THIRD IS THE ONE THAT MATTERS
-----------------------------------------------------
  E35                     the 9 legs whose atr_stop_mult/tp_r changed in 892c9a2c
  NON-E35 trend/pullback  same FAMILY, same venue, untouched by e35
  NON-E35 scalp/other     everything else on the venue

A two-arm split (e35 vs everything) cannot separate *"the geometry broke these
legs"* from *"the trend family broke and the e35 legs are all trend legs"* — all
nine are trend/pullback while the rest of the book is dominated by ict_scalp.
The family-matched control is the arm that separates them, and it is why this
does not stop at the obvious comparison.

WHAT IS CONTROLLED, AND WHY EACH EXCLUSION IS NOT CHERRY-PICKING
----------------------------------------------------------------
  * **pairs_\\*** — EXONERATED by the row on its own measurement (post-break net
    +$29.69 over 120 closes) and its exit mix moved for a KNOWN reason: hedge
    mode was armed the same day so the sleeve stopped half-opening. Leaving it
    in would mix a fixed thing into a broken thing.
  * **exit_reason == 'netting_attributed'** — these closes carry FABRICATED pnl
    (OI-20260908); their win/loss label is not an observation.
  * **bybit only** — the e35 legs are all Bybit crypto. An unmatched control
    containing IB futures and Alpaca equities would let "crypto degraded more"
    masquerade as the e35 effect.
  * **split on `created_at`, never `closed_at`** — bracket geometry is fixed at
    ENTRY, so a trade OPENED before the deploy carries the OLD geometry however
    late it closes. This is the sharpening
    OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED had to make
    to its own clause (b) after a trade satisfied the letter and proved the
    wrong thing.

⚠️ WHAT IT CANNOT DO, STATED UP FRONT. Win RATE is the robust statistic here and
PnL deliberately is not reported: the book mixes a futures multiplier, paper and
real money, and 200 of the closed rows carry manufactured provenance, so a PnL
sum is dominated by whichever arm happens to hold the ib_paper rows. A session
wanting money must filter by `src.runtime.provenance` first.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import pathlib
import sys

#: The 9 legs 892c9a2c changed. DERIVED, not transcribed: produced by parsing
#: config/strategies.yaml at 892c9a2c^ and 892c9a2c and diffing
#: atr_stop_mult/tp_r per strategy. Pinned here so the arm is reproducible on a
#: shallow clone that no longer has the commit.
E35_LEGS = frozenset({
    "ada_pullback_2h", "avax_pullback_2h", "htf_pullback_trend_2h",
    "trend_donchian", "trend_donchian_ada_4h", "trend_donchian_avax_4h",
    "trend_donchian_eth_4h", "trend_donchian_sol_4h", "trend_donchian_xrp_4h",
})

#: 892c9a2c's commit time. The DEPLOY follows within ~5 minutes (ict-git-sync),
#: and no trade opened in that window, so the commit time is used directly
#: rather than inventing a deploy timestamp nobody recorded.
DEPLOY = dt.datetime(2026, 8, 30, 8, 53, 19, tzinfo=dt.timezone.utc)


def _parse(value):
    try:
        x = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001 — an unparseable stamp is DROPPED, never bucketed
        return None
    return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)


def _is_family(name: str) -> bool:
    """Trend/pullback, the family every e35 leg belongs to."""
    return (name.startswith("trend_donchian")
            or name.endswith("_pullback_2h")
            or name.startswith("htf_pullback"))


def population(rows: list[dict]) -> tuple[list[dict], dict]:
    """The graded population plus the census of what was dropped and why."""
    census = collections.Counter()
    keep = []
    for r in rows:
        name = str(r.get("strategy_name") or "")
        if r.get("status") != "closed":
            census["not_closed"] += 1; continue
        if r.get("is_backtest"):
            census["backtest"] += 1; continue
        if r.get("pnl") is None:
            census["pnl_null"] += 1; continue
        if not str(r.get("account_id") or "").startswith("bybit"):
            census["off_venue"] += 1; continue
        if name.startswith("pairs_"):
            census["pairs_sleeve_exonerated"] += 1; continue
        if r.get("exit_reason") == "netting_attributed":
            census["fabricated_close"] += 1; continue
        if _parse(r.get("created_at")) is None:
            census["created_at_unparseable"] += 1; continue
        keep.append(r)
    return keep, dict(census)


def _rate(rs):
    n = len(rs)
    w = sum(1 for r in rs if float(r.get("pnl") or 0) > 0)
    return n, w, (100.0 * w / n if n else None)


def grade(rows: list[dict]) -> dict:
    pop, census = population(rows)
    arms = {
        "e35": [r for r in pop if r["strategy_name"] in E35_LEGS],
        "control_same_family": [r for r in pop if r["strategy_name"] not in E35_LEGS
                                and _is_family(r["strategy_name"])],
        "control_other": [r for r in pop if r["strategy_name"] not in E35_LEGS
                          and not _is_family(r["strategy_name"])],
    }
    out = {"population_n": len(pop), "excluded": census, "deploy": DEPLOY.isoformat(),
           "arms": {}}
    for key, rs in arms.items():
        pre = [r for r in rs if _parse(r["created_at"]) < DEPLOY]
        post = [r for r in rs if _parse(r["created_at"]) >= DEPLOY]
        n0, w0, r0 = _rate(pre)
        n1, w1, r1 = _rate(post)
        out["arms"][key] = {
            "n_pre": n0, "wins_pre": w0, "win_rate_pre": r0,
            "n_post": n1, "wins_post": w1, "win_rate_post": r1,
            # `None`, never 0.0, when either side is empty: "no trades" is not
            # "no change", and a 0.0 here would be read as the latter.
            "delta_pp": (r1 - r0) if (r0 is not None and r1 is not None) else None,
            "legs_post": dict(collections.Counter(r["strategy_name"] for r in post)),
        }
    e, c = out["arms"]["e35"], out["arms"]["control_same_family"]
    # The ONE inferential statement this makes, and it is deliberately a
    # LEVEL comparison in the POST period rather than a difference of deltas:
    # the family-matched control's PRE arm is far too thin to anchor a delta,
    # and pretending otherwise is how a 6-trade baseline becomes a headline.
    if e["n_post"] and c["n_post"] and c["win_rate_post"] is not None:
        p = c["wins_post"] / c["n_post"]
        k, n = e["wins_post"], e["n_post"]
        out["p_value_one_sided"] = sum(
            math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))
        out["p_basis"] = (f"P(<= {k} wins in {n} | p = the family-matched control's "
                          f"own POST rate {p:.3f}); one-sided binomial")
    else:
        out["p_value_one_sided"] = None
        out["p_basis"] = "not computable — an arm is empty"
    return out


def render(v: dict) -> str:
    lines = [f"e35-break-attribution: population n={v['population_n']}, split at {v['deploy']}",
             f"  excluded: {v['excluded']}", ""]
    lines.append(f"  {'arm':24} {'n_pre':>5} {'win_pre':>8} | {'n_post':>6} {'win_post':>9} | {'delta':>8}")
    for k, a in v["arms"].items():
        f = lambda x: f"{x:.1f}%" if x is not None else "  n/a"
        d = f"{a['delta_pp']:+.1f}pp" if a["delta_pp"] is not None else "n/a"
        lines.append(f"  {k:24} {a['n_pre']:>5} {f(a['win_rate_pre']):>8} | "
                     f"{a['n_post']:>6} {f(a['win_rate_post']):>9} | {d:>8}")
    lines.append("")
    lines.append(f"  {v['p_basis']} -> "
                 f"{'n/a' if v['p_value_one_sided'] is None else format(v['p_value_one_sided'], '.4f')}")
    lines.append("")
    lines.append("  ⚠️ THIS IS A SIGNAL, NOT A VERDICT, AND THE SCRIPT SAYS SO RATHER THAN")
    lines.append("     LEAVING IT TO THE READER: the e35 post arm is n=20-ish and the")
    lines.append("     family-matched control's PRE arm is n=6. Re-run on a fresh journal")
    lines.append("     pull before anyone reverts a Tier-3 parameter on it.")
    return "\n".join(lines)


def _self_test() -> int:
    fails = []
    def ok(c, label):
        if not c: fails.append(label)
        print(f"  {'ok ' if c else 'FAIL'} {label}")

    def row(leg, when, pnl, **kw):
        d = {"strategy_name": leg, "created_at": when, "pnl": pnl, "status": "closed",
             "is_backtest": 0, "account_id": "bybit_1"}
        d.update(kw); return d
    PRE, POST = "2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z"

    v = grade([row("trend_donchian", PRE, 1.0), row("trend_donchian", POST, -1.0)])
    ok(v["arms"]["e35"]["win_rate_pre"] == 100.0 and v["arms"]["e35"]["win_rate_post"] == 0.0,
       "an e35 leg is bucketed by created_at, pre and post")
    ok(v["arms"]["control_same_family"]["n_pre"] == 0
       and v["arms"]["control_same_family"]["win_rate_pre"] is None,
       "an EMPTY arm reports None, never 0.0 — 'no trades' is not 'no wins'")

    v = grade([row("trend_donchian_eth", POST, 1.0)])
    ok(v["arms"]["control_same_family"]["n_post"] == 1,
       "a trend leg NOT in the e35 set lands in the family-matched control")
    v = grade([row("ict_scalp_5m", POST, 1.0)])
    ok(v["arms"]["control_other"]["n_post"] == 1, "a scalp leg lands in the other arm")

    v = grade([row("pairs_sol_eth_a", POST, 1.0),
               row("trend_donchian", POST, 1.0, exit_reason="netting_attributed"),
               row("trend_donchian", POST, 1.0, account_id="ib_paper"),
               row("trend_donchian", POST, None)])
    ok(v["population_n"] == 0, "every declared exclusion is applied")
    ok(set(v["excluded"]) == {"pairs_sleeve_exonerated", "fabricated_close",
                              "off_venue", "pnl_null"},
       "…and each is CENSUSED by name, so a shrinking population is never silent")

    v = grade([row("trend_donchian", POST, -1.0) for _ in range(20)]
              + [row("trend_donchian_eth", POST, 1.0) for _ in range(5)]
              + [row("trend_donchian_eth", POST, -1.0) for _ in range(5)])
    ok(v["p_value_one_sided"] is not None and v["p_value_one_sided"] < 0.01,
       "0 of 20 against a 50% control is a small p — the statistic is wired to the arms")
    v = grade([row("trend_donchian", POST, -1.0)])
    ok(v["p_value_one_sided"] is None and "not computable" in v["p_basis"],
       "…and with an empty control it REFUSES rather than dividing by zero")

    print(f"self-test: {'PASS' if not fails else 'FAIL'} ({len(fails)} failure(s))")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal", help="path to a saved /api/diag/journal?table=trades payload")
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
    v = grade(rows)
    print(json.dumps(v, indent=2) if a.json else render(v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
