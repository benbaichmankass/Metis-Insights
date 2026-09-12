#!/usr/bin/env python3
# wiring: manual-only - a one-off decomposition of a DATED event (the winner-size
# collapse MI-271 measured across 2026-08-30). A scheduled runner would re-answer
# it every day against a moving population and quietly turn a recorded verdict
# into a drifting one. The standing, cadenced version of the underlying question
# is the sustained-losing-streak detector proposed in MI-271 section 6, which is
# a DIFFERENT deliverable and is deliberately not this file.
"""Decompose the winner-size collapse (MI-277 / WO-20260912).

THE QUESTION
------------
MI-271 (`docs/research/bleed-attribution-2026-09-11.md`) measured, on `bybit_1`:
average win $404 -> $112 (-72%) while win rate moved only 4.9pp. The expectancy
flip +$40 -> -$144 is therefore a WINNER-SIZE collapse. Nobody established why.

PHASE 1 COMES FIRST, AND IT IS NOT DUE DILIGENCE.
The cycle priority (`CY-20260906-TRADING-TRUTH`) is explicit: repair the
MEASUREMENT before acting on what it says. Provenance coverage roughly DOUBLED
across the split point (MI-271 section 1 caveat 3), so a naive before/after
average-win comparison is partly comparing a poorly-measured period against a
well-measured one. If a material share of the "wins" in either era is ESTIMATED,
the -72% may be partly an instrument artifact -- and saying so IS the finding.

PHASE 2 -- THE DECOMPOSITION IDENTITY, AND WHY IT IS THIS ONE
`src/units/accounts/risk.py::_size_unbounded` is the one sizer:

    qty = (balance * risk_pct) / (|entry - sl| * contract_value)

so for any trade the dollar risk at the stop is

    risk_usd = |entry - sl| * qty * contract_value  ==  balance * risk_pct

which is INVARIANT to stop width (MI-275 section 4 established this and it is
imported here, not re-derived). Every closed trade therefore satisfies exactly

    pnl = risk_usd * R          where R := pnl / risk_usd

and an average win in DOLLARS decomposes with no residual as

    mean(pnl | win) = mean(risk_usd) * mean(R) + cov(risk_usd, R)

That is the whole decomposition, and it maps one-to-one onto the candidate
mechanisms the unit was dispatched with:

  * mean(risk_usd) moved   -> POSITION SIZE fell (balance, risk_pct, a clamp,
                              conviction sizing, the cash-settlement gate)
  * mean(R) moved          -> EXIT DISTANCE shrank (winners closing nearer
                              entry, in the unit the bracket is denominated in)
  * cov moved              -> the two stopped lining up
  * per-strategy shares    -> COMPOSITION shifted (a fleet average can fall with
                              no single leg changing)

STATES ARE NEVER COLLAPSED. `risk_usd` is None -- never 0.0 -- when a row
carries no usable stop; such rows are counted as `ungradeable_no_risk` and are
NEVER folded into the graded set. A zero is a real reading here (a stop at the
entry price) and must stay distinguishable from "we could not look".

CONTRACT VALUE. Every account this decomposition reports on trades LINEAR USDT
perpetuals, where contract_value is 1 and `position_size` is denominated in the
base asset, so risk_usd = |entry - sl| * position_size directly. That premise is
ASSERTED, not assumed: `identity_check` re-derives pnl from (exit - entry) *
size * direction and reports the residual distribution. A venue where it does
not hold shows up there as a systematic residual rather than silently biasing
every R.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.runtime import provenance as P  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from bleed_attribution_2026_09_11 import (  # noqa: E402
    E35_DEPLOY_UTC,
    group_of,
    population,
)

# The split point is e35's deploy, imported from MI-271 so the two memos can
# never silently disagree about where the eras divide.
SPLIT = E35_DEPLOY_UTC


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def era_of(row: dict) -> str:
    """PRE/POST by OPEN time. A leg carries the geometry it was opened under."""
    ca = row.get("created_at")
    if not ca:
        return "unknown"
    return "post" if str(ca) >= SPLIT else "pre"


def risk_usd(row: dict, pkgs: dict | None = None):
    """Dollar risk at the ENTRY-FROZEN stop, or None when it cannot be established.

    ⚠️ ``trades.stop_loss`` IS THE WRONG FIELD AND USING IT SILENTLY BREAKS THE
    DECOMPOSITION. It is the FINAL, TRAILED stop -- trailing levers amend it in
    place -- so for a winner that trailed, ``|entry - stop_loss|`` is the
    distance to a stop that was dragged up behind the move, not the risk the
    position was sized against. MI-275 section 3.4 established the same field
    distinction for exit adjudication; the consequence here is arithmetically
    worse, because the quantity sits in a DENOMINATOR. Measured on this
    population: ``bybit_1`` trade 5027 reads ``|entry - stop_loss| = 0.000885``
    on SOL, giving risk $0.88 and R = 3672 on a $3,225 win -- one row that
    single-handedly moves the pre-era mean R from ~15 to 102.6.

    So the basis is ``order_packages.sl`` / ``.entry``, which are entry-frozen
    (MI-275 section 3.3 proved this basis lands on each leg's declared ATR
    multiplier to three decimals). ``trades.entry_price`` is the FILL and
    ``order_packages.entry`` the SIGNAL level; the bot sizes against the
    package, so the package is what is used, and the two are reported.

    Returns ``(value, state)``:
      ``graded``            -- positive frozen risk distance and a size
      ``no_pkg``            -- no ``order_package_id``: *we could not look*
      ``pkg_not_in_window`` -- package outside the 1000-row pull: *we could not
                               look*, and emphatically NOT "no risk"
      ``pkg_no_levels``     -- package carries no entry/sl
      ``no_size``           -- no position size
      ``zero_distance``     -- stop AT the entry: a REAL reading, but R is
                               undefined, so it is graded separately and never
                               folded into a *could not look* state.
    """
    size = _f(row.get("position_size"))
    opid = row.get("order_package_id")
    if not opid:
        return None, "no_pkg"
    pkg = (pkgs or {}).get(opid)
    if pkg is None:
        return None, "pkg_not_in_window"
    entry, stop = _f(pkg.get("entry")), _f(pkg.get("sl"))
    if entry is None or stop is None:
        return None, "pkg_no_levels"
    if size is None:
        return None, "no_size"
    dist = abs(entry - stop)
    if dist <= 0:
        return None, "zero_distance"
    return dist * abs(size), "graded"


def risk_usd_trailed(row: dict):
    """The ``trades.stop_loss`` basis, kept ONLY as a reported sensitivity.

    It is what a naive reading would use. It is carried so the memo can show how
    far the two bases диverge rather than merely asserting that they do.
    """
    entry, stop, size = (
        _f(row.get("entry_price")),
        _f(row.get("stop_loss")),
        _f(row.get("position_size")),
    )
    if None in (entry, stop, size):
        return None
    d = abs(entry - stop)
    return (d * abs(size)) if d > 0 else None


def enrich(rows: list[dict], pkgs: dict | None = None) -> list[dict]:
    out = []
    for r in rows:
        d = dict(r)
        d["_era"] = era_of(r)
        d["_group"] = group_of(str(r.get("strategy_name") or ""))
        d["_prov"], d["_prov_why"] = P.classify_pnl(r)
        ru, st = risk_usd(r, pkgs)
        d["_risk_usd"] = ru
        d["_risk_state"] = st
        rt = risk_usd_trailed(r)
        d["_risk_trailed"] = rt
        d["_R_trailed"] = (_f(r.get("pnl")) / rt) if rt else None
        pnl = _f(r.get("pnl"))
        d["_pnl"] = pnl
        d["_R"] = (pnl / ru) if (ru and pnl is not None) else None
        out.append(d)
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _cov(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 2:
        return None
    mx = statistics.fmean(p[0] for p in pairs)
    my = statistics.fmean(p[1] for p in pairs)
    # Population covariance -- this is an exact algebraic decomposition of the
    # observed mean, not an estimate of a parameter, so n (not n-1) is correct.
    return sum((p[0] - mx) * (p[1] - my) for p in pairs) / len(pairs)


def decompose(rows: list[dict]) -> dict:
    """The identity mean(pnl) = mean(risk)*mean(R) + cov(risk, R), on winners.

    Only rows whose risk is ``graded`` can enter, because R is undefined
    otherwise. The ungraded rows are COUNTED and reported beside the result --
    never dropped silently, and never imputed.
    """
    wins = [r for r in rows if (r["_pnl"] or 0) > 0]
    graded = [r for r in wins if r["_risk_state"] == "graded"]
    ungraded = collections.Counter(
        r["_risk_state"] for r in wins if r["_risk_state"] != "graded"
    )
    risks = [r["_risk_usd"] for r in graded]
    Rs = [r["_R"] for r in graded]
    pnls = [r["_pnl"] for r in graded]
    mr, mR = _mean(risks), _mean(Rs)
    cov = _cov(risks, Rs)
    out = {
        "n_rows": len(rows),
        "n_wins": len(wins),
        "n_wins_graded": len(graded),
        "win_rate": (len(wins) / len(rows)) if rows else None,
        "avg_win_all_wins": _mean([r["_pnl"] for r in wins]),
        "avg_win_graded": _mean(pnls),
        "mean_risk_usd": mr,
        "median_risk_usd": _median(risks),
        "mean_R": mR,
        "median_R": _median(Rs),
        "cov_risk_R": cov,
        "identity_lhs": _mean(pnls),
        "identity_rhs": (mr * mR + cov) if (mr is not None and mR is not None and cov is not None) else None,
        "ungradeable_wins": dict(ungraded),
        # The mean identity is EXACT but a mean is outlier-dominated, and this
        # population has real outliers. The medians are reported beside it so a
        # reader cannot take the mean alone -- neither replaces the other.
        "median_pnl_win": _median(pnls),
        # Same decomposition on the naive trailed-stop basis, so the memo can
        # SHOW the divergence rather than assert it.
        "mean_R_trailed_basis": _mean(
            [r["_R_trailed"] for r in graded if r["_R_trailed"] is not None]
        ),
        "median_R_trailed_basis": _median(
            [r["_R_trailed"] for r in graded if r["_R_trailed"] is not None]
        ),
    }
    lhs, rhs = out["identity_lhs"], out["identity_rhs"]
    # An assertion INSIDE the transform -- the only self-verification that has
    # historically worked in this repo (CLAUDE-RULES-CANONICAL "WHAT ENFORCES
    # THIS RULE"). If the algebra does not close, every number above is wrong.
    out["identity_closes"] = (
        lhs is not None and rhs is not None and abs(lhs - rhs) <= max(1e-6, abs(lhs) * 1e-9)
    )
    return out


def identity_check(rows: list[dict]) -> dict:
    """Assert contract_value == 1, rather than assuming it.

    Re-derives pnl from (exit - entry) * size * direction and reports the
    residual as a fraction of |pnl|. A venue whose contract multiplier is not 1
    shows up as a systematic residual concentrated on that venue.
    """
    per_account = collections.defaultdict(list)
    for r in rows:
        e, x, s = _f(r.get("entry_price")), _f(r.get("exit_price")), _f(r.get("position_size"))
        pnl = r["_pnl"]
        if None in (e, x, s) or pnl is None or abs(pnl) < 1e-9:
            continue
        sign = 1.0 if str(r.get("direction") or "").lower().startswith(("long", "buy")) else -1.0
        derived = (x - e) * abs(s) * sign
        per_account[r.get("account_id")].append(derived / pnl)
    return {
        acct: {
            "n": len(v),
            "median_derived_over_recorded": round(statistics.median(v), 4),
        }
        for acct, v in sorted(per_account.items())
    }


def by_key(rows, keyfn):
    g = collections.defaultdict(list)
    for r in rows:
        g[keyfn(r)].append(r)
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--packages", required=True)
    ap.add_argument("--account", default="bybit_1")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    with open(a.trades) as fh:
        raw = json.load(fh)
    with open(a.packages) as fh:
        pkg_rows = json.load(fh)
    pkgs = {p["order_package_id"]: p for p in pkg_rows if p.get("order_package_id")}
    pop = enrich(population(raw), pkgs)

    result = {
        "population": {
            "source": a.trades,
            "rows_pulled": len(raw),
            "id_min": min(r["id"] for r in raw),
            "id_max": max(r["id"] for r in raw),
            "created_min": min(r["created_at"] for r in raw),
            "created_max": max(r["created_at"] for r in raw),
            "decision_population": len(pop),
            "filter": "status=closed AND NOT is_backtest AND pnl IS NOT NULL AND NOT pairs sleeve (MI-271 population(), imported)",
            "split_utc": SPLIT,
            "split_field": "created_at (OPEN time)",
            "packages_pulled": len(pkg_rows),
            "packages_created_min": min(p["created_at"] for p in pkg_rows),
            "packages_created_max": max(p["created_at"] for p in pkg_rows),
            "risk_basis": "order_packages.sl / .entry (ENTRY-FROZEN) -- NOT trades.stop_loss, which is the trailed stop",
            "risk_join_states": dict(collections.Counter(r["_risk_state"] for r in pop)),
        },
        "contract_value_check": identity_check(pop),
    }

    acct = [r for r in pop if r.get("account_id") == a.account]
    result["account"] = a.account
    result["headline"] = {
        era: decompose([r for r in acct if r["_era"] == era]) for era in ("pre", "post")
    }

    # PHASE 1 -- is the collapse measured? Split winners by provenance bucket.
    ph1 = {}
    for era in ("pre", "post"):
        rows = [r for r in acct if r["_era"] == era]
        wins = [r for r in rows if (r["_pnl"] or 0) > 0]
        ph1[era] = {
            "n_rows": len(rows),
            "rows_by_prov": dict(collections.Counter(r["_prov"] for r in rows)),
            "n_wins": len(wins),
            "wins_by_prov": dict(collections.Counter(r["_prov"] for r in wins)),
            "avg_win_by_prov": {
                b: round(_mean([r["_pnl"] for r in wins if r["_prov"] == b]) or 0, 2)
                for b in sorted(set(r["_prov"] for r in wins))
            },
            "pnl_by_prov": {
                b: round(sum(r["_pnl"] for r in rows if r["_prov"] == b), 2)
                for b in sorted(set(r["_prov"] for r in rows))
            },
            "exit_src": dict(
                collections.Counter(P.classify_row(r, "exit_price_source")[1] for r in wins)
            ),
            "measured_only": decompose([r for r in rows if r["_prov"] == P.MEASURED]),
            "estimated_only": decompose([r for r in rows if r["_prov"] == P.ESTIMATED]),
        }
    result["phase1_provenance"] = ph1
    result["phase1_standardised"] = provenance_standardised(acct)

    # PHASE 2ab -- attribute the CHANGE in mean win across the identity's terms.
    result["phase2_factor_attribution"] = factor_attribution(
        result["headline"]["pre"], result["headline"]["post"]
    )

    # PHASE 2c -- composition. Per-strategy, so a fleet average that falls with
    # no single leg changing is distinguishable from one where legs changed.
    comp = {}
    for era in ("pre", "post"):
        rows = [r for r in acct if r["_era"] == era]
        per = {}
        for strat, rs in by_key(rows, lambda r: r.get("strategy_name")).items():
            d = decompose(rs)
            per[strat] = {
                "n": d["n_rows"],
                "n_wins": d["n_wins"],
                "avg_win": round(d["avg_win_all_wins"], 2) if d["avg_win_all_wins"] else None,
                "mean_risk_usd": round(d["mean_risk_usd"], 2) if d["mean_risk_usd"] else None,
                "mean_R": round(d["mean_R"], 4) if d["mean_R"] is not None else None,
                "total_pnl": round(sum(x["_pnl"] for x in rs), 2),
            }
        comp[era] = per
    result["phase2_composition"] = comp

    # PHASE 2d -- counterfactual reweighting. Hold the leg MIX at its PRE shares
    # and apply POST per-leg avg wins, and vice versa. This separates "the mix
    # changed" from "the legs changed" WITHOUT asserting either.
    result["phase2_shift_share"] = shift_share(acct)

    # The same decomposition on every account, so a bybit_1-only story cannot be
    # mistaken for a fleet one.
    result["by_account"] = {
        acc: {
            era: {
                k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in decompose([r for r in rs if r["_era"] == era]).items()
                if k in ("n_rows", "n_wins", "avg_win_all_wins", "mean_risk_usd", "mean_R", "win_rate")
            }
            for era in ("pre", "post")
        }
        for acc, rs in sorted(by_key(pop, lambda r: r.get("account_id")).items())
    }

    result["annexes"] = annexes(pop, pkgs, raw, a.account)

    txt = json.dumps(result, indent=2, default=str)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(txt)
    print(txt)
    return 0


def _hours(row):
    import datetime as _dt

    def _p(v):
        try:
            return _dt.datetime.fromisoformat(str(v))
        except (TypeError, ValueError):
            return None

    a, b = _p(row.get("created_at")), _p(row.get("closed_at"))
    return ((b - a).total_seconds() / 3600.0) if (a and b) else None


def annexes(pop, pkgs, raw, account):
    """Every remaining measurement the memo quotes, so all of it reproduces.

    Each annex answers ONE candidate mechanism and is named for it, so a reader
    can see which candidates were tested and which were not.
    """
    acct = [r for r in pop if r.get("account_id") == account]
    byb = [r for r in pop if str(r.get("account_id") or "").startswith("bybit")]
    out = {}

    # A1 -- THE FILTER. MI-271's headline table is reproduced on BOTH filters,
    # because the qualitative conclusion differs between them.
    def basic(rows):
        w = [r for r in rows if (_f(r.get("pnl")) or 0) > 0]
        losers = [r for r in rows if (_f(r.get("pnl")) or 0) <= 0]
        if not rows:
            return None
        p = len(w) / len(rows)
        aw = _mean([_f(r["pnl"]) for r in w])
        al = _mean([_f(r["pnl"]) for r in losers])
        return {
            "n": len(rows), "win_rate": round(p, 4),
            "avg_win": round(aw, 2) if aw else None,
            "avg_loss": round(al, 2) if al else None,
            "expectancy": round(p * (aw or 0) + (1 - p) * (al or 0), 2),
        }

    closed_all = [
        r for r in raw
        if r.get("status") == "closed" and not r.get("is_backtest")
        and r.get("pnl") is not None and r.get("account_id") == account
    ]
    out["A1_filter_sensitivity"] = {
        "note": "MI-271 section 4.3's table is the PAIRS-INCLUDED row. Every other section of that memo excludes the pairs sleeve. The two disagree qualitatively about whether this is a win-rate collapse.",
        "pairs_included": {e: basic([r for r in closed_all if era_of(r) == e]) for e in ("pre", "post")},
        "pairs_excluded": {e: basic([r for r in acct if r["_era"] == e]) for e in ("pre", "post")},
    }

    # A2 -- SIZE, over the WHOLE book rather than winners only. A winners-only
    # read of risk is a SELECTION of the trades that happened to win, and on
    # this population it points the opposite way to the full book.
    out["A2_size_full_book_vs_winners"] = {
        "note": "risk_usd on WINNERS ONLY is a selection effect; the full book is the test of whether sizing moved.",
        **{
            f"{era}_{scope}": {
                "n": len(g),
                "median_risk_usd": round(_median([r["_risk_usd"] for r in g]), 1),
                "mean_risk_usd": round(_mean([r["_risk_usd"] for r in g]), 1),
            }
            for era in ("pre", "post")
            for scope, g in (
                ("all_rows", [r for r in acct if r["_era"] == era]),
                ("winners_only", [r for r in acct if r["_era"] == era and (r["_pnl"] or 0) > 0]),
            )
        },
    }

    # A3 -- THE LOSER SIDE AS A CONTROL. If the sizer or the stops had moved,
    # loss R would move. It does not, which is what isolates the winner side.
    out["A3_loser_control"] = {
        era: {
            "n_losers": len([r for r in acct if r["_era"] == era and (r["_pnl"] or 0) <= 0]),
            "median_R": round(_median([r["_R"] for r in acct if r["_era"] == era and (r["_pnl"] or 0) <= 0]), 4),
            "mean_R": round(_mean([r["_R"] for r in acct if r["_era"] == era and (r["_pnl"] or 0) <= 0]), 4),
        }
        for era in ("pre", "post")
    }

    # A4 -- HOLD TIME. Involves NO exit price, so it cannot be a provenance
    # artifact of any kind.
    out["A4_hold_time_price_free"] = {
        era: {
            "median_hours_winners": round(_median([_hours(r) for r in byb if r["_era"] == era and (r["_pnl"] or 0) > 0]), 2),
            "median_hours_losers": round(_median([_hours(r) for r in byb if r["_era"] == era and (r["_pnl"] or 0) <= 0]), 2),
            "win_rate_pooled_bybit": round(
                len([r for r in byb if r["_era"] == era and (r["_pnl"] or 0) > 0])
                / len([r for r in byb if r["_era"] == era]), 4),
            "n_pooled_bybit": len([r for r in byb if r["_era"] == era]),
        }
        for era in ("pre", "post")
    }

    # A5 -- THE VENUE CAP. cap_r = TP_VENUE_CAP_PCT * entry / risk_distance.
    from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT

    def cap_cell(era):
        w = [r for r in acct if r["_era"] == era and (r["_pnl"] or 0) > 0]
        caps, ratio, tpr = [], [], []
        for r in w:
            q = pkgs.get(r["order_package_id"]) or {}
            en, sl, tp = _f(q.get("entry")), _f(q.get("sl")), _f(q.get("tp"))
            if not en or sl is None or abs(en - sl) <= 0:
                continue
            rd = abs(en - sl)
            cr = TP_VENUE_CAP_PCT * en / rd
            caps.append(cr)
            ratio.append(r["_R"] / cr)
            if tp:
                tpr.append(abs(tp - en) / rd)
        return {
            "n": len(caps),
            "median_cap_r": round(_median(caps), 2),
            "median_declared_tp_r": round(_median(tpr), 2) if tpr else None,
            "median_achieved_R_over_cap_r": round(_median(ratio), 4),
            "frac_within_10pct_of_cap": round(sum(1 for x in ratio if x >= 0.9) / len(ratio), 4),
        }

    out["A5_venue_cap"] = {
        "TP_VENUE_CAP_PCT": TP_VENUE_CAP_PCT,
        "note": "If the clamp were truncating winners there would be a pile-up at cap_r.",
        **{era: cap_cell(era) for era in ("pre", "post")},
    }

    # A6 -- EXIT LEVERS. The competing SYSTEM explanation for winners exiting
    # sooner: a trailing / stale / giveback lever cutting them early.
    def lever_cell(era):
        w = [r for r in acct if r["_era"] == era and (r["_pnl"] or 0) > 0]
        trail = 0
        for r in w:
            ep = (pkgs.get(r["order_package_id"]) or {}).get("exit_plan")
            try:
                d = json.loads(ep) if isinstance(ep, str) else (ep or {})
            except (TypeError, ValueError):
                d = {}
            if d.get("trailing_stop") or (d.get("rungs") or []):
                trail += 1
        return {
            "n_winners": len(w),
            "exit_reasons": dict(collections.Counter(r.get("exit_reason") for r in w)),
            "median_R_by_exit_reason": {
                k: round(_median([r["_R"] for r in w if r.get("exit_reason") == k]), 3)
                for k in sorted({r.get("exit_reason") for r in w})
            },
            "packages_declaring_trailing_or_rungs": trail,
        }

    out["A6_exit_levers"] = {era: lever_cell(era) for era in ("pre", "post")}

    # A7 -- VOLATILITY, via stop distance in bp of entry. The ict_scalp stops
    # are ATR-derived and their multiplier was never changed, so their stop
    # distance is a proxy for realised vol at signal time.
    def bp_list(era, leg=None):
        v = []
        for r in acct:
            if r["_era"] != era or (leg and r["strategy_name"] != leg):
                continue
            q = pkgs.get(r["order_package_id"]) or {}
            en, sl = _f(q.get("entry")), _f(q.get("sl"))
            if en and sl is not None:
                v.append(1e4 * abs(en - sl) / en)
        return v

    out["A7_volatility_proxy"] = {
        "note": "stop distance in bp of entry; ict_scalp multipliers were never changed, so a rise is a vol rise",
        "pooled_median_bp": {e: round(_median(bp_list(e)), 1) for e in ("pre", "post")},
    }

    # A8 -- THE 15.0 bp FLOOR. A hard clamp found in the data whose SOURCE was
    # NOT located in src/ this session. Recorded as measured-but-unattributed.
    FLOOR = 0.0015
    def floor_rows(era, at):
        out_ = []
        for r in acct:
            if r["_era"] != era:
                continue
            q = pkgs.get(r["order_package_id"]) or {}
            en, sl = _f(q.get("entry")), _f(q.get("sl"))
            if not en or sl is None:
                continue
            hit = abs(abs(en - sl) / en - FLOOR) < 5e-6
            if hit == at:
                out_.append(r)
        return out_

    out["A8_stop_distance_floor"] = {
        "value": FLOOR,
        "source_in_src": "NOT LOCATED this session -- searched src/ for the literal, min_stop*, *_bps and clamp/floor names; the only hit is hf_vwap_revert's unrelated min_stop_pct=0.003. Treat the value as MEASURED and its origin as UNESTABLISHED.",
        "direction_inference": "INFERRED a FLOOR rather than a cap: a cap binds MORE as volatility rises, a floor binds LESS; vol rose (A7) and the share at the value FELL 34.2% -> 20.8%.",
        **{
            era: {
                "n_rows": len(floor_rows(era, True)) + len(floor_rows(era, False)),
                "n_at_floor": len(floor_rows(era, True)),
                "winners_excluding_floor": {
                    "n": len([r for r in floor_rows(era, False) if (r["_pnl"] or 0) > 0]),
                    "median_R": round(_median([r["_R"] for r in floor_rows(era, False) if (r["_pnl"] or 0) > 0]), 4),
                    "avg_win": round(_mean([r["_pnl"] for r in floor_rows(era, False) if (r["_pnl"] or 0) > 0]), 2),
                },
            }
            for era in ("pre", "post")
        },
    }

    # A9 -- PER-LEG SIGN TEST. A fleet average can fall with no leg changing;
    # this asks whether the legs themselves moved, and in which direction.
    legs = []
    for L in sorted({r["strategy_name"] for r in acct}):
        a = [r["_pnl"] for r in acct if r["strategy_name"] == L and r["_era"] == "pre" and r["_pnl"] > 0]
        b = [r["_pnl"] for r in acct if r["strategy_name"] == L and r["_era"] == "post" and r["_pnl"] > 0]
        if a and b:
            legs.append((L, statistics.fmean(a), statistics.fmean(b)))
    down = sum(1 for _, a, b in legs if b < a)
    n = len(legs)
    p = min(1.0, 2 * sum(math.comb(n, k) for k in range(down, n + 1)) / (2 ** n)) if n else None
    out["A9_per_leg_sign_test"] = {
        "legs_winning_in_both_eras": n,
        "legs_whose_avg_win_fell": down,
        "exact_two_sided_binomial_p": round(p, 5) if p is not None else None,
        "per_leg": [
            {"leg": L, "pre_avg_win": round(a, 2), "post_avg_win": round(b, 2), "pct": round(100 * (b - a) / a, 1)}
            for L, a, b in legs
        ],
    }
    return out


def provenance_standardised(acct: list[dict]) -> dict:
    """Re-weight each era's winners to the OTHER era's provenance mix.

    Provenance coverage roughly TRIPLED across the split on this account, so a
    raw before/after average-win comparison is partly comparing a poorly-measured
    period against a well-measured one (MI-271 section 1 caveat 3). This holds the
    mix fixed and asks what the collapse reads then.

    ⚠️ THIS IS A STANDARDISATION, NOT A CORRECTION. It assumes only that within
    a bucket the observed mean is the right estimate for that bucket -- it does
    not claim an estimated row's pnl is right. Its VALUE is directional: it says
    which way the coverage shift pushed the headline.
    """
    def cells(rows):
        w = [r for r in rows if (r["_pnl"] or 0) > 0]
        out = {}
        for b in (P.MEASURED, P.ESTIMATED, P.FABRICATED, P.UNVERIFIED):
            v = [r["_pnl"] for r in w if r["_prov"] == b]
            out[b] = {"n": len(v), "avg": (statistics.fmean(v) if v else None)}
        return out, len(w)

    pre_c, pre_n = cells([r for r in acct if r["_era"] == "pre"])
    post_c, post_n = cells([r for r in acct if r["_era"] == "post"])

    def standardise(cells_from, weights_from, wtotal):
        """Apply `cells_from` bucket means at `weights_from` bucket shares.

        Buckets present in the weights but EMPTY in the cells cannot be
        standardised; they are named and the result is renormalised over the
        buckets that exist, rather than imputed.
        """
        usable = [b for b in cells_from if cells_from[b]["avg"] is not None and weights_from[b]["n"]]
        if not usable or not wtotal:
            return None, []
        w = sum(weights_from[b]["n"] for b in usable)
        missing = [b for b in weights_from if weights_from[b]["n"] and b not in usable]
        return sum(weights_from[b]["n"] * cells_from[b]["avg"] for b in usable) / w, missing

    post_at_pre_mix, m1 = standardise(post_c, pre_c, pre_n)
    pre_at_post_mix, m2 = standardise(pre_c, post_c, post_n)
    actual_pre = _mean([r["_pnl"] for r in acct if r["_era"] == "pre" and (r["_pnl"] or 0) > 0])
    actual_post = _mean([r["_pnl"] for r in acct if r["_era"] == "post" and (r["_pnl"] or 0) > 0])
    out = {
        "pre_win_cells": {k: {"n": v["n"], "avg": round(v["avg"], 2) if v["avg"] else None} for k, v in pre_c.items()},
        "post_win_cells": {k: {"n": v["n"], "avg": round(v["avg"], 2) if v["avg"] else None} for k, v in post_c.items()},
        "actual_pre_avg_win": round(actual_pre, 2),
        "actual_post_avg_win": round(actual_post, 2),
        "actual_pct_change": round(100.0 * (actual_post - actual_pre) / actual_pre, 1),
        "post_at_pre_provenance_mix": round(post_at_pre_mix, 2) if post_at_pre_mix else None,
        "pre_at_post_provenance_mix": round(pre_at_post_mix, 2) if pre_at_post_mix else None,
        "buckets_unstandardisable_post": m1,
        "buckets_unstandardisable_pre": m2,
    }
    if post_at_pre_mix:
        out["pct_change_at_pre_mix"] = round(100.0 * (post_at_pre_mix - actual_pre) / actual_pre, 1)
    return out


def factor_attribution(pre: dict, post: dict) -> dict:
    """Split the change in mean win across mean(risk), mean(R) and cov.

    mean(pnl|win) = mean(risk)*mean(R) + cov(risk, R), so the CHANGE is

        d[mean(pnl)] = dRisk*R_pre  +  risk_pre*dR  +  dRisk*dR  +  dCov
                       \_size_/       \___R___/       \_inter_/    \_cov_/

    The interaction term is reported SEPARATELY rather than being split
    between the two main effects by convention. Which convention you pick
    changes each share by a few points, and a decomposition whose shares depend
    on an unstated convention is exactly the unprovenanced output this repo has
    a guard for. The residual is asserted to be zero.
    """
    mr0, mR0, c0 = pre["mean_risk_usd"], pre["mean_R"], pre["cov_risk_R"]
    mr1, mR1, c1 = post["mean_risk_usd"], post["mean_R"], post["cov_risk_R"]
    if None in (mr0, mR0, c0, mr1, mR1, c1):
        return {"state": "ungradeable_missing_term"}
    total = post["avg_win_graded"] - pre["avg_win_graded"]
    size = (mr1 - mr0) * mR0
    rterm = mr0 * (mR1 - mR0)
    inter = (mr1 - mr0) * (mR1 - mR0)
    cov = c1 - c0
    resid = total - (size + rterm + inter + cov)
    return {
        "avg_win_pre": round(pre["avg_win_graded"], 2),
        "avg_win_post": round(post["avg_win_graded"], 2),
        "total_change": round(total, 2),
        "pct_change": round(100.0 * total / pre["avg_win_graded"], 1),
        "size_effect_usd": round(size, 2),
        "R_effect_usd": round(rterm, 2),
        "interaction_usd": round(inter, 2),
        "cov_effect_usd": round(cov, 2),
        "size_share_pct": round(100.0 * size / total, 1),
        "R_share_pct": round(100.0 * rterm / total, 1),
        "interaction_share_pct": round(100.0 * inter / total, 1),
        "cov_share_pct": round(100.0 * cov / total, 1),
        "residual_usd": round(resid, 6),
        "residual_is_zero": abs(resid) <= max(1e-6, abs(total) * 1e-9),
        "mean_risk_usd_pre_post": [round(mr0, 2), round(mr1, 2)],
        "mean_R_pre_post": [round(mR0, 4), round(mR1, 4)],
        "median_risk_usd_pre_post": [
            round(pre["median_risk_usd"], 2),
            round(post["median_risk_usd"], 2),
        ],
        "median_R_pre_post": [round(pre["median_R"], 4), round(post["median_R"], 4)],
    }


def shift_share(acct: list[dict]) -> dict:
    """Separate a MIX change from a PER-LEG change, without asserting either.

    Overall avg win = sum over legs of (leg share of wins) * (leg avg win).
    Two counterfactuals:
      ``post_legs_pre_mix``  -- POST per-leg avg wins at PRE win shares
      ``pre_legs_post_mix``  -- PRE per-leg avg wins at POST win shares
    Legs present in only one era cannot be reweighted; they are COUNTED and the
    counterfactual is reported over the common legs only, with that restriction
    named. Silently dropping them would manufacture a clean decomposition.
    """
    def shares_and_avgs(rows):
        wins = [r for r in rows if (r["_pnl"] or 0) > 0]
        per = collections.defaultdict(list)
        for r in wins:
            per[r.get("strategy_name")].append(r["_pnl"])
        total = len(wins)
        return (
            {k: len(v) / total for k, v in per.items()} if total else {},
            {k: statistics.fmean(v) for k, v in per.items()},
            total,
        )

    pre_sh, pre_avg, pre_n = shares_and_avgs([r for r in acct if r["_era"] == "pre"])
    post_sh, post_avg, post_n = shares_and_avgs([r for r in acct if r["_era"] == "post"])
    common = sorted(set(pre_avg) & set(post_avg))

    def blend(shares, avgs, legs):
        w = sum(shares.get(leg, 0.0) for leg in legs)
        if w <= 0:
            return None
        return sum(shares.get(leg, 0.0) * avgs[leg] for leg in legs) / w

    return {
        "common_legs": len(common),
        "pre_only_legs": sorted(set(pre_avg) - set(post_avg)),
        "post_only_legs": sorted(set(post_avg) - set(pre_avg)),
        "restriction": "counterfactuals computed over legs winning in BOTH eras only; legs listed above are excluded and named rather than dropped",
        "pre_actual_common": blend(pre_sh, pre_avg, common),
        "post_actual_common": blend(post_sh, post_avg, common),
        "post_legs_pre_mix": blend(pre_sh, post_avg, common),
        "pre_legs_post_mix": blend(post_sh, pre_avg, common),
        "pre_win_total": pre_n,
        "post_win_total": post_n,
    }


if __name__ == "__main__":
    raise SystemExit(main())
