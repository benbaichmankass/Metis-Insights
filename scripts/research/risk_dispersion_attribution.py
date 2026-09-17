#!/usr/bin/env python3
# wiring: manual-only — a one-shot audit answering "why does realised per-trade
#   risk disperse across legs on ONE account with ONE declared risk_pct?". Run BY
#   A SESSION against a journal pull; scheduling it would re-ask the question
#   over a window nobody chose.
"""MI-278 U47 — why does realised per-trade risk disperse on one account?

THE ROW
--------
`BL-20260913-EVERY-PROFITABLE-LEG-IS-SIZED-SMALLER-PER-UNIT-OF-RISK-THAN-EVERY-LOSING-ONE-ON-REAL-MONEY`
measured dollars-per-unit-R spanning **3.09×** across the six live real-money
legs, separating perfectly by sign of edge. Its `resolution_criteria` offers two
exits, and this module is built for the first:

> (a) the dispersion is ATTRIBUTED — the 3.09x spread is traced to a named
> mechanism (per-account risk config, per-leg stop width, venue minimums,
> conviction sizing, or a mix) with the population stated

THE ALGEBRA THAT MAKES THIS A CLOSED QUESTION
----------------------------------------------
A leg's dollars-per-R is ``|Σpnl / ΣR|``, and per trade ``R = pnl / risk_usd``,
so dollars-per-R is a **risk-weighted mean of `risk_usd`**. And
``risk.py::_size_unbounded`` computes ``raw_qty = balance × risk_pct /
risk_distance``, i.e.

    risk_usd  =  position_size × risk_per_unit  =  balance × risk_pct

**exactly**, on any row nothing clamped. On an account declaring ONE
``risk_pct`` for every leg, dispersion in realised `risk_usd` is therefore
either a CLAMP or the BALANCE — and the clamps are a **finite, readable list**
inside ``position_size``. That is what makes attribution a closed enumeration
rather than a hunt.

THE CLAMP LIST, read off `src/units/accounts/risk.py` rather than recalled
------------------------------------------------------------------------------
``lot_floor`` (``_floor_to_step``) · ``sub_min_refusal`` (returns 0.0, so it
never appears in a sized row) · ``whole_unit_roundup`` (equity/futures only) ·
``daily_loss_budget`` (scales qty by ``loss_budget_remaining``) ·
``margin_ceiling`` (``max_qty_by_margin``) · ``exposure_ceiling``
(``max_qty_by_exposure``, only when a gross-exposure policy is declared). Plus
two terms outside ``position_size`` that reach the same number:
``confidence_scalar`` (``risk_pct × _confidence_scalar``) and
``post_entry_rewrite`` — netting attribution overwrites ``position_size`` on a
row that stays open (MI-278 U33), so a closed row's size need not be the one
the sizer chose.

FOUR VERDICTS, NEVER COLLAPSED
-------------------------------
``unreachable``
    The mechanism CANNOT run on this account, established by reading config or
    code — not by a correlation. The strongest verdict available, and the one a
    correlation can never give.
``refuted``
    Reachable, and the evidence says it is not the driver. Carries its statistic
    and its population.
``contributes``
    Reachable and evidenced on some rows, but not sufficient to explain the
    spread. Named with the rows it does explain.
``untestable``
    ***We could not look.*** The input the test needs is not on any surface this
    session can read. **This is NOT a pass and NOT a refutation**, and folding it
    into either is how a hunt ends with a false verdict.

⚠️ **AN ATTRIBUTION IS ONLY COMPLETE WHEN SOMETHING IS `contributes` OR
`supported` AND THE RESIDUE IS ACCOUNTED FOR.** A run in which every candidate
is `unreachable`/`refuted`/`untestable` has **not** attributed anything — it has
narrowed the field and named what it could not read, and `verdict` says exactly
that rather than implying a conclusion.

WHAT IT DOES NOT DO
-------------------
It changes nothing and proposes no resize. Per-leg risk sizing is **Tier-3**;
`src/units/accounts/risk.py` and `config/accounts.yaml` are read only. It makes
no claim about any leg's edge.

Usage:
  python3 scripts/research/risk_dispersion_attribution.py --self-test
  python3 scripts/research/risk_dispersion_attribution.py --journal j.json --packages p.json --account bybit_2
  python3 scripts/research/risk_dispersion_attribution.py --journal j.json --packages p.json --account bybit_2 --exposure-soak e.json --json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from src.runtime.r_provenance import declared_initial_risk  # noqa: E402

try:
    from src.units.accounts.risk import (  # noqa: E402
        _MARGIN_SAFETY_BUFFER as MARGIN_BUFFER,
        WHOLE_UNIT_QTY_EXCHANGES,
    )
except ImportError:  # pragma: no cover — narrow on purpose
    MARGIN_BUFFER, WHOLE_UNIT_QTY_EXCHANGES = None, frozenset()

UNREACHABLE = "unreachable"
REFUTED = "refuted"
CONTRIBUTES = "contributes"
UNTESTABLE = "untestable"

# Keys netting attribution stamps onto `notes`. Both live under the
# `dump_capped(notes, 500)` cap and neither is protected, so a TRUNCATED notes
# blob is *we could not look* — never *not attributed* (MI-278 U33).
_NETTING_KEYS = ("netting_attribution_basis", "netting_attributed_qty")


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (f != f or f in (float("inf"), float("-inf"))) else f


def parse_blob(v):
    if isinstance(v, (str, bytes, bytearray)):
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            return {}
    return v if isinstance(v, dict) else {}


def pearson(xs, ys):
    """Pearson r, or None when either series has no variance. Never raises."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / ((vx * vy) ** 0.5)


def realised_risk(row, package):
    """``position_size × declared_initial_risk``, or None.

    ⚠️ This is the risk the row ENDED UP carrying, not necessarily the one the
    sizer chose — see the `post_entry_rewrite` candidate.
    """
    rpu = declared_initial_risk((package or {}).get("meta"))
    ps = _num(row.get("position_size"))
    if rpu is None or ps is None or ps <= 0:
        return None
    return ps * rpu


def build_population(trades, packages, *, account_id, since=None):
    """Closed, non-backtest rows on one account with a usable realised risk."""
    pidx = {p.get("order_package_id"): p for p in (packages or [])
            if p.get("order_package_id")}
    out, skipped = [], collections.Counter()
    for t in trades or []:
        if t.get("account_id") != account_id:
            skipped["other_account"] += 1
            continue
        if t.get("is_backtest"):
            skipped["backtest"] += 1
            continue
        if t.get("status") != "closed" or _num(t.get("pnl")) is None:
            skipped["not_closed_with_pnl"] += 1
            continue
        when = t.get("closed_at") or t.get("created_at") or ""
        if since and when < since:
            skipped["before_window"] += 1
            continue
        pkg = pidx.get(t.get("order_package_id"))
        if pkg is None:
            skipped["package_absent_from_pull"] += 1
            continue
        risk = realised_risk(t, pkg)
        if risk is None:
            skipped["no_declared_risk_or_size"] += 1
            continue
        out.append({"row": t, "pkg": pkg, "risk": risk})
    return out, dict(skipped)


def budget_estimate(pop):
    """The largest realised risk seen — an ESTIMATE of the unclamped budget.

    ⚠️ It is an estimate, not a declared value: nothing publishes the
    decision-time budget, so the maximum observed is the tightest lower bound
    available. Every ``realised/B`` below is therefore bounded by 1 BY
    CONSTRUCTION, which is fine for a correlation (scale-invariant) and must
    never be read as "no row exceeded its budget".
    """
    if not pop:
        return None
    return max(p["risk"] for p in pop)


def candidate_verdicts(pop, *, account_cfg, exposure_rows=None,
                       risk_pct=None, leverage=None):
    """Grade every clamp in `position_size` plus the two outside it."""
    B = budget_estimate(pop)
    frac = [p["risk"] / B for p in pop] if B else []
    out = {}

    risk_block = dict((account_cfg or {}).get("risk") or {})
    exchange = str((account_cfg or {}).get("exchange") or "").lower()
    market_type = str((account_cfg or {}).get("market_type") or "").lower()
    rp = risk_pct if risk_pct is not None else _num(risk_block.get("risk_pct"))
    lev = leverage if leverage is not None else _num(risk_block.get("leverage"))

    # --- 1. per-leg risk config: a CONFIG READ, not a correlation -----------
    legs = sorted({p["row"].get("strategy_name") for p in pop})
    out["per_leg_risk_config"] = {
        "verdict": UNREACHABLE if rp is not None else UNTESTABLE,
        "basis": (f"the account declares ONE risk.risk_pct ({rp}) and the sizer "
                  f"reads no per-strategy risk field, so all {len(legs)} legs "
                  f"share a budget by construction"
                  if rp is not None else
                  "risk.risk_pct is not readable for this account"),
    }

    # --- 2. conviction sizing ------------------------------------------------
    mode = str(risk_block.get("confidence_sizing") or "off").strip().lower()
    out["confidence_scalar"] = {
        "verdict": UNREACHABLE if mode == "off" else UNTESTABLE,
        "basis": (f"risk.confidence_sizing resolves to {mode!r}, so "
                  "_confidence_scalar returns 1.0 for every trade"
                  if mode == "off" else
                  f"confidence_sizing is {mode!r}; the per-trade scalar is not "
                  "recorded on any readable surface"),
    }

    # --- 3. whole-unit round-up ---------------------------------------------
    whole = exchange in {str(x).lower() for x in WHOLE_UNIT_QTY_EXCHANGES}
    fut = market_type == "futures"
    out["whole_unit_roundup"] = {
        "verdict": UNTESTABLE if (whole or fut) else UNREACHABLE,
        "basis": (f"exchange {exchange!r} is not in WHOLE_UNIT_QTY_EXCHANGES and "
                  f"market_type is {market_type!r}, so the round-up branch "
                  "cannot run" if not (whole or fut) else
                  "the round-up branch is reachable on this account"),
    }

    # --- 4. gross-exposure ceiling: settled by the policy, not a correlation -
    if exposure_rows is None:
        out["exposure_ceiling"] = {
            "verdict": UNTESTABLE,
            "basis": "no --exposure-soak supplied, so whether a gross-exposure "
                     "policy is declared for this account is *we did not look*",
        }
    else:
        decl = [bool(r.get("policy_declared")) for r in exposure_rows]
        any_decl = any(decl)
        out["exposure_ceiling"] = {
            "verdict": UNTESTABLE if (any_decl or not decl) else UNREACHABLE,
            "basis": (f"policy_declared is False on all {len(decl)} soak rows and "
                      "max_gross_exposure_pct is None, so exposure_headroom_usd() "
                      "returns None and the clamp never runs"
                      if decl and not any_decl else
                      f"a gross-exposure policy IS declared on "
                      f"{sum(decl)}/{len(decl)} soak rows" if decl else
                      "no soak row for this account"),
            "rows_seen": len(decl),
        }

    # --- 5. instrument lot floor --------------------------------------------
    steps = {}
    try:
        from src.units.accounts.qty_legalize import instrument_lot
        for p in pop:
            sym = p["row"].get("symbol") or ""
            if sym not in steps:
                lot = instrument_lot(sym)
                steps[sym] = lot[0] if lot else None
    except Exception:  # noqa: BLE001  # allow-silent: an unresolvable lot table makes the test UNTESTABLE below, which is the honest outcome — it is never folded into `refuted`.
        steps = {}
    lots, explained = [], 0
    for p in pop:
        st = steps.get(p["row"].get("symbol") or "")
        q = _num(p["row"].get("position_size"))
        if not st or q is None or st <= 0:
            continue
        L = q / st
        lots.append(L)
        # flooring can only lose the fractional part; an integer L loses nothing
        if abs(L - round(L)) > 1e-9:
            explained += 1
    if not lots:
        out["lot_floor"] = {"verdict": UNTESTABLE,
                            "basis": "no instrument lot step resolved"}
    else:
        out["lot_floor"] = {
            "verdict": CONTRIBUTES if explained else REFUTED,
            "basis": (f"{len(lots) - explained} of {len(lots)} sized rows are an "
                      f"EXACT multiple of their instrument lot, so flooring lost "
                      f"them nothing; {explained} carry a fractional remainder"),
            "rows_with_remainder": explained,
            "rows_graded": len(lots),
        }

    # --- 6. daily-loss-budget scaling ---------------------------------------
    r_daily = _daily_corr(pop, B)
    out["daily_loss_budget"] = {
        "verdict": REFUTED if (r_daily is not None and abs(r_daily) < 0.2)
        else (UNTESTABLE if r_daily is None else CONTRIBUTES),
        "basis": "correlation of realised/B against the day's realised PnL "
                 "BEFORE the trade opened",
        "pearson_r": (round(r_daily, 4) if r_daily is not None else None),
    }

    # --- 7. margin pre-flight cap -------------------------------------------
    ratios, fr = [], []
    for p, f in zip(pop, frac):
        e = _num(p["pkg"].get("entry"))
        rpu = declared_initial_risk(p["pkg"].get("meta"))
        if e and rpu and rp and lev and MARGIN_BUFFER:
            ratios.append(rp * e / (rpu * lev * MARGIN_BUFFER))
            fr.append(f)
    r_margin = pearson(ratios, fr) if ratios else None
    out["margin_ceiling"] = {
        "verdict": REFUTED if (r_margin is not None and abs(r_margin) < 0.2)
        else (UNTESTABLE if r_margin is None else CONTRIBUTES),
        "basis": "correlation of realised/B against the equity-free "
                 "margin-bound ratio (MI-278 U45)",
        "pearson_r": (round(r_margin, 4) if r_margin is not None else None),
        "provably_bound_rows": sum(1 for x in ratios if x >= 1.0),
        "rows_graded": len(ratios),
    }

    # --- 8. post-entry rewrite by netting attribution ------------------------
    stamped = [i for i, p in enumerate(pop)
               if any(k in parse_blob(p["row"].get("notes")) for k in _NETTING_KEYS)]
    truncated = sum(1 for p in pop
                    if parse_blob(p["row"].get("notes")).get("_truncated"))
    if truncated and not stamped:
        v = UNTESTABLE
    elif stamped:
        v = CONTRIBUTES
    else:
        v = REFUTED
    lowest = sorted(range(len(frac)), key=lambda i: frac[i])[:2] if frac else []
    out["post_entry_rewrite"] = {
        "verdict": v,
        "basis": ("netting attribution overwrites position_size on a row that "
                  "stays open (MI-278 U33), so a closed row's size need not be "
                  "the sizer's"),
        "stamped_rows": len(stamped),
        "rows_graded": len(pop),
        "notes_truncated_we_could_not_look": truncated,
        "both_lowest_rows_stamped": bool(lowest) and all(i in stamped for i in lowest),
    }
    return out, B


def _daily_corr(pop, B):
    if not B:
        return None
    by_day = collections.defaultdict(list)
    for p in pop:
        c = p["row"].get("closed_at") or ""
        if c:
            by_day[c[:10]].append((c, _num(p["row"].get("pnl")) or 0.0))
    xs, ys = [], []
    for p in pop:
        o = p["row"].get("created_at") or ""
        if not o:
            continue
        prior = sum(v for c, v in by_day.get(o[:10], []) if c < o)
        xs.append(prior)
        ys.append(p["risk"] / B)
    return pearson(xs, ys)


def report(trades, packages, *, account_id, account_cfg=None, since=None,
           exposure_rows=None, risk_pct=None, leverage=None):
    pop, skipped = build_population(trades, packages, account_id=account_id,
                                    since=since)
    verdicts, B = candidate_verdicts(
        pop, account_cfg=account_cfg, exposure_rows=exposure_rows,
        risk_pct=risk_pct, leverage=leverage)
    per_leg = collections.defaultdict(list)
    for p in pop:
        per_leg[p["row"].get("strategy_name")].append(p["risk"])
    legs = {k: {"n": len(v), "median_risk_usd": round(statistics.median(v), 4),
                "min": round(min(v), 4), "max": round(max(v), 4)}
            for k, v in sorted(per_leg.items(),
                               key=lambda kv: statistics.median(kv[1]))}
    counts = collections.Counter(d["verdict"] for d in verdicts.values())
    attributed = counts.get(CONTRIBUTES, 0) > 0
    return {
        "account_id": account_id,
        "since": since,
        "population": {"graded_rows": len(pop), "skipped": skipped,
                       "legs": len(per_leg)},
        "budget_estimate": (round(B, 4) if B else None),
        "realised_over_budget": ({
            "min": round(min(p["risk"] for p in pop) / B, 4),
            "max": round(max(p["risk"] for p in pop) / B, 4),
            "spread": round(max(p["risk"] for p in pop)
                            / min(p["risk"] for p in pop), 4),
        } if pop and B else None),
        "per_leg": legs,
        "candidates": verdicts,
        "verdict": (
            "attributed_in_part" if attributed else
            ("no_candidate_survives — the field is NARROWED, nothing is "
             "attributed, and the residue is unexplained")
        ),
        "counts": dict(counts),
    }


def render(v):
    L = [f"REALISED-RISK DISPERSION — account {v['account_id']}"
         + (f", closed_at >= {v['since']}" if v["since"] else "")]
    p = v["population"]
    L.append(f"  POPULATION: {p['graded_rows']} graded rows over {p['legs']} legs")
    for k, n in sorted(p["skipped"].items()):
        L.append(f"     skipped {k}: {n}")
    if v["realised_over_budget"]:
        r = v["realised_over_budget"]
        L.append(f"  budget estimate B = {v['budget_estimate']} (the LARGEST realised "
                 "risk seen — an estimate, not a declared value)")
        L.append(f"  realised/B spans {r['min']} .. {r['max']}  (spread {r['spread']}x)")
    L.append("")
    L.append("  PER LEG — realised risk_usd = position_size x declared risk_per_unit:")
    for leg, d in v["per_leg"].items():
        L.append(f"     {leg:26s} n={d['n']:3d}  median {d['median_risk_usd']:8.3f}  "
                 f"min {d['min']:8.3f}  max {d['max']:8.3f}")
    L.append("")
    L.append("  CANDIDATES — every clamp in position_size, plus the two outside it:")
    for name, d in v["candidates"].items():
        extra = " ".join(f"{k}={d[k]}" for k in
                         ("pearson_r", "stamped_rows", "rows_with_remainder",
                          "provably_bound_rows", "rows_graded", "rows_seen",
                          "notes_truncated_we_could_not_look")
                         if k in d and d[k] is not None)
        L.append(f"     {name:22s} {d['verdict']:12s} {extra}")
        L.append(f"        {d['basis']}")
    L.append("")
    L.append(f"  VERDICT: {v['verdict']}")
    L.append("  ⚠️ `unreachable` and `refuted` are NOT an attribution, and `untestable`")
    L.append("     is *we could not look* — never a pass and never a refutation.")
    return "\n".join(L)


# ---------------------------------------------------------------------------
def _self_test():
    fails, ran = [], [0]

    def ck(name, cond):
        ran[0] += 1
        if not cond:
            fails.append(name)

    ck("margin buffer imported", MARGIN_BUFFER == 0.9)
    ck("whole-unit set imported", "alpaca" in {str(x).lower() for x in WHOLE_UNIT_QTY_EXCHANGES})
    ck("canonical declared-risk reader is imported, not copied",
       declared_initial_risk.__module__.endswith("r_provenance"))

    # --- pearson -------------------------------------------------------------
    ck("pearson is 1 on a perfect line", abs(pearson([1, 2, 3], [2, 4, 6]) - 1.0) < 1e-9)
    ck("pearson is -1 on a perfect inverse", abs(pearson([1, 2, 3], [6, 4, 2]) + 1.0) < 1e-9)
    ck("pearson None with no variance", pearson([1, 1, 1], [1, 2, 3]) is None)
    ck("pearson None below n=3", pearson([1, 2], [1, 2]) is None)

    def pkg(pid, entry, rpu):
        return {"order_package_id": pid, "entry": entry,
                "meta": json.dumps({"risk_per_unit": rpu})}

    def row(rid, pid, size, pnl=1.0, **kw):
        d = {"id": rid, "account_id": "acct", "order_package_id": pid,
             "position_size": size, "status": "closed", "pnl": pnl,
             "strategy_name": "leg_a", "symbol": "BTCUSDT",
             "created_at": "2026-09-01T00:00:00", "closed_at": "2026-09-01T01:00:00"}
        d.update(kw)
        return d

    pkgs = [pkg("p1", 100.0, 1.0), pkg("p2", 100.0, 2.0)]
    rows = [row(1, "p1", 4.0), row(2, "p2", 2.0, strategy_name="leg_b"),
            row(3, "p1", 3.0)]

    # --- population ----------------------------------------------------------
    pop, sk = build_population(rows, pkgs, account_id="acct")
    ck("population built", len(pop) == 3)
    ck("realised risk is size x declared risk", pop[0]["risk"] == 4.0)
    ck("other accounts are skipped and counted",
       build_population(rows + [row(9, "p1", 1.0, account_id="other")], pkgs,
                        account_id="acct")[1]["other_account"] == 1)
    ck("a backtest row is skipped",
       build_population(rows + [row(9, "p1", 1.0, is_backtest=True)], pkgs,
                        account_id="acct")[1]["backtest"] == 1)
    ck("an absent package is skipped and counted",
       build_population([row(9, "missing", 1.0)], pkgs,
                        account_id="acct")[1]["package_absent_from_pull"] == 1)
    ck("a row before the window is skipped",
       build_population(rows, pkgs, account_id="acct",
                        since="2026-09-02")[1]["before_window"] == 3)

    ck("budget is the max realised risk", budget_estimate(pop) == 4.0)
    ck("budget None on an empty population", budget_estimate([]) is None)

    # --- the four verdicts are all reachable ---------------------------------
    cfg_clean = {"exchange": "bybit", "market_type": "linear",
                 "risk": {"risk_pct": 0.015, "leverage": 3}}
    exp_none = [{"policy_declared": False, "max_gross_exposure_pct": None}] * 5
    v, B = candidate_verdicts(pop, account_cfg=cfg_clean, exposure_rows=exp_none)
    ck("one declared risk_pct makes per-leg config UNREACHABLE",
       v["per_leg_risk_config"]["verdict"] == UNREACHABLE)
    ck("confidence_sizing off is UNREACHABLE",
       v["confidence_scalar"]["verdict"] == UNREACHABLE)
    ck("bybit linear makes whole-unit UNREACHABLE",
       v["whole_unit_roundup"]["verdict"] == UNREACHABLE)
    ck("an undeclared exposure policy is UNREACHABLE",
       v["exposure_ceiling"]["verdict"] == UNREACHABLE)
    ck("no soak supplied is UNTESTABLE, never unreachable",
       candidate_verdicts(pop, account_cfg=cfg_clean,
                          exposure_rows=None)[0]["exposure_ceiling"]["verdict"]
       == UNTESTABLE)
    ck("a DECLARED exposure policy is not called unreachable",
       candidate_verdicts(pop, account_cfg=cfg_clean, exposure_rows=[
           {"policy_declared": True}])[0]["exposure_ceiling"]["verdict"] == UNTESTABLE)

    # --- conviction sizing ON must not read as unreachable -------------------
    cfg_conv = json.loads(json.dumps(cfg_clean))
    cfg_conv["risk"]["confidence_sizing"] = "linear"
    ck("confidence_sizing linear is UNTESTABLE, not unreachable",
       candidate_verdicts(pop, account_cfg=cfg_conv,
                          exposure_rows=exp_none)[0]["confidence_scalar"]["verdict"]
       == UNTESTABLE)

    # --- whole-unit reachable on alpaca / futures ----------------------------
    for bad in ({"exchange": "alpaca", "market_type": "linear", "risk": {"risk_pct": 0.02}},
                {"exchange": "ib", "market_type": "futures", "risk": {"risk_pct": 0.015}}):
        ck(f"whole-unit reachable on {bad['exchange']}/{bad['market_type']}",
           candidate_verdicts(pop, account_cfg=bad, exposure_rows=exp_none
                              )[0]["whole_unit_roundup"]["verdict"] == UNTESTABLE)

    # --- lot floor: an EXACT multiple loses nothing --------------------------
    ck("lot floor REFUTED when every qty is an exact multiple",
       v["lot_floor"]["verdict"] in (REFUTED, UNTESTABLE))

    # --- post-entry rewrite --------------------------------------------------
    stamped_rows = [row(1, "p1", 1.0, notes=json.dumps(
        {"netting_attributed_qty": 3.0})), row(2, "p1", 4.0), row(3, "p1", 3.0)]
    pop2, _ = build_population(stamped_rows, pkgs, account_id="acct")
    v2, _ = candidate_verdicts(pop2, account_cfg=cfg_clean, exposure_rows=exp_none)
    ck("a netting stamp makes post_entry_rewrite CONTRIBUTES",
       v2["post_entry_rewrite"]["verdict"] == CONTRIBUTES)
    ck("the stamped count is reported", v2["post_entry_rewrite"]["stamped_rows"] == 1)
    ck("no stamp anywhere is REFUTED",
       v["post_entry_rewrite"]["verdict"] == REFUTED)
    trunc = [row(1, "p1", 1.0, notes=json.dumps({"_truncated": True})),
             row(2, "p1", 4.0), row(3, "p1", 3.0)]
    pop3, _ = build_population(trunc, pkgs, account_id="acct")
    v3, _ = candidate_verdicts(pop3, account_cfg=cfg_clean, exposure_rows=exp_none)
    ck("a TRUNCATED notes blob is UNTESTABLE, never refuted",
       v3["post_entry_rewrite"]["verdict"] == UNTESTABLE)
    ck("the truncated count is reported",
       v3["post_entry_rewrite"]["notes_truncated_we_could_not_look"] == 1)

    # --- THE headline: nothing surviving is NOT an attribution ---------------
    # A FLAT population: every risk identical, so both correlations have no
    # variance and grade UNTESTABLE rather than producing a spurious r on n=3.
    flat = [row(1, "p1", 4.0), row(2, "p1", 4.0, strategy_name="leg_b"),
            row(3, "p1", 4.0)]
    rep = report(flat, pkgs, account_id="acct", account_cfg=cfg_clean,
                 exposure_rows=exp_none)
    ck("no candidate CONTRIBUTES on the flat population",
       rep["counts"].get(CONTRIBUTES, 0) == 0)
    ck("a run with no CONTRIBUTES does not claim an attribution",
       rep["verdict"].startswith("no_candidate_survives"))
    ck("a flat population makes both correlations UNTESTABLE, not refuted",
       rep["candidates"]["daily_loss_budget"]["verdict"] == UNTESTABLE
       and rep["candidates"]["margin_ceiling"]["verdict"] == UNTESTABLE)
    rep2 = report(stamped_rows, pkgs, account_id="acct", account_cfg=cfg_clean,
                  exposure_rows=exp_none)
    ck("a run with a CONTRIBUTES says attributed_in_part",
       rep2["verdict"] == "attributed_in_part")
    ck("the verdict counts are published", isinstance(rep["counts"], dict))
    rep3 = report(rows, pkgs, account_id="acct", account_cfg=cfg_clean,
                  exposure_rows=exp_none)
    ck("per-leg medians are ordered ascending",
       list(rep3["per_leg"]) == sorted(rep3["per_leg"],
                                       key=lambda k: rep3["per_leg"][k]["median_risk_usd"]))
    # risks are 4.0 (p1 x 4), 4.0 (p2 rpu 2 x 2) and 3.0 -> max/min = 4/3
    ck("realised/B spread is the max-over-min of realised risk",
       rep3["realised_over_budget"]["spread"] == 1.3333)

    # --- empty population is refused, not graded as clean --------------------
    empty = report([], [], account_id="acct", account_cfg=cfg_clean)
    ck("an empty population reports no budget", empty["budget_estimate"] is None)
    ck("an empty population reports no spread", empty["realised_over_budget"] is None)
    ck("an empty population still does not claim an attribution",
       empty["verdict"].startswith("no_candidate_survives"))

    for r_ in (rep, rep2, empty):
        try:
            render(r_)
        except Exception as exc:  # noqa: BLE001  # allow-silent: this IS the assertion — the control is that render() raises for NO reachable report, so every catch becomes a named FAIL rather than a swallowed error.
            fails.append(f"render raised: {exc}")

    total = ran[0]
    print(f"self-test: {total - len(fails)}/{total} PASS")
    for f in fails:
        print("  FAIL:", f)
    return 1 if fails else 0


def _load(path):
    with open(path) as fh:
        d = json.load(fh)
    if isinstance(d, dict):
        for k in ("rows", "items", "lines"):
            if isinstance(d.get(k), list):
                return [json.loads(x) if isinstance(x, str) else x
                        for x in d[k] if str(x).strip()]
    return d if isinstance(d, list) else []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal")
    ap.add_argument("--packages")
    ap.add_argument("--account", default="bybit_2")
    ap.add_argument("--since", default=None)
    ap.add_argument("--exposure-soak",
                    help="a /api/diag/log_file?name=exposure_soak pull — without "
                         "it the exposure ceiling is UNTESTABLE, not unreachable")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()
    if not a.journal or not a.packages:
        ap.error("--journal and --packages are both required")

    try:
        from src.config.accounts_loader import load_accounts_dict
        cfg = dict((load_accounts_dict() or {}).get(a.account) or {})
    except (ImportError, OSError, ValueError) as exc:
        print(f"could not read accounts.yaml through the canonical loader "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)
        return 2

    exp = None
    if a.exposure_soak:
        exp = [r for r in _load(a.exposure_soak)
               if isinstance(r, dict) and r.get("account_id") == a.account]

    v = report(_load(a.journal), _load(a.packages), account_id=a.account,
               account_cfg=cfg, since=a.since, exposure_rows=exp)
    print(json.dumps(v, indent=2) if a.json else render(v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
