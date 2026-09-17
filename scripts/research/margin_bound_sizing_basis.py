#!/usr/bin/env python3
# wiring: manual-only — a one-shot audit answering "was this order's size a RISK
#   decision or a MARGIN ceiling?". Run BY A SESSION against a journal pull;
#   scheduling it would re-ask the question over a window nobody chose.
"""MI-278 U45 — is this order's size a RISK decision, or the margin ceiling?

THE ROW THIS ANSWERS, AND THE BLOCKER IT DECLARES
--------------------------------------------------
`PB-20260822-AVAX-SCALP-SIZED-OFF-MARGIN-NOT-RISK` (performance backlog, Tier-3,
severity high) says `ict_scalp_avax_5m`'s order size IS `max_qty_by_margin`, and
its `resolution_criteria` opens::

    notes.margin_basis records the PRE-CLAMP risk-derived qty beside
    max_qty_by_margin, so 'the risk model asked for X and got the ceiling
    instead' is readable per order rather than inferable from two rows.

That is a **Tier-2 stamp in `src/units/accounts/risk.py`** and this lane may not
write it. This module asks the prior question: **how much of that sentence is
readable per order from what is ALREADY recorded?**

THE ANSWER — AN EQUITY-FREE, PER-ORDER PROOF
---------------------------------------------
The risk sizer asks for a notional of ``equity x risk_pct x entry / risk_per_unit``
(``risk.py::_size_unbounded``, ``raw_qty = balance x risk_pct / risk_distance``).
The margin ceiling is ``basis_usd x leverage x buffer`` (``risk.py::position_size``,
the 2026-05-12 margin pre-flight cap). Every basis the sizer can use —
``venue_available``, ``equity_unadjusted``, ``free_balance`` — is **at most the
account's equity**, so::

    demanded_notional     equity x risk_pct x entry / risk_per_unit
    ----------------- >= -------------------------------------------
    ceiling_notional          equity x leverage x buffer

                       =   risk_pct x entry / (risk_per_unit x leverage x buffer)

The equity term **cancels**. When that ratio is >= 1 the margin cap MUST bind, at
any balance, and no new stamp is needed to say so. Equivalently the cap binds
whenever the stop distance is below ``10000 x risk_pct / (leverage x buffer)``
basis points — **55.56 bp** on an account declaring 1.5% risk at 3x with the
0.9 safety buffer.

⚠️ IT IS SUFFICIENT, NOT NECESSARY, AND THAT IS THE WHOLE REASON FOR
``not_provable``
--------------------------------------------------------------------
The inequality is slack by exactly ``equity / basis_usd``, and on a book holding
positions the venue's *available* margin is a small fraction of equity. Measured
2026-09-17 over the 32 `bybit_1` rows that carry both a stamped
``notes.margin_basis`` and a declared ``risk_per_unit``: the test called **8**
of the **30** rows that were observed sitting exactly on their own stamped
ceiling, and produced **ZERO false positives**. So a `not_provable` row is
***we did not look***, never *risk-derived*, and reporting a bound RATE without
the coverage beside it would be a false-negative rate wearing a finding's
clothes.

⚠️ `position_size` IS NOT ALWAYS A SIZER OUTPUT — A SECOND, ORTHOGONAL AXIS
----------------------------------------------------------------------------
Three journal populations carry a `position_size` that
``RiskManager.position_size`` never produced, and inverting any of them for a
risk number is meaningless:

* ``setup_type='intent_reduce'`` — the intent multiplexer's REDUCTION quantity
  (the row also carries ``notes.intent_target_qty`` / ``intent_current_qty``).
* ``setup_type='adopted_orphan'`` — a size read off the VENUE and adopted.
* the **pairs sleeve** — ``src/units/strategies/pairs_executor.py`` is an
  isolated order path called once per tick from ``src/main.py`` and never
  through ``multi_account_execute``; its own comment at `pairs_executor.py:1043`
  contrasts its budget with *"the same basis RiskManager.position_size uses for
  every other strategy"*.

This module grades that axis separately (`qty_provenance`) and never folds it
into the sizing verdict.

WHAT IT DOES NOT DO
-------------------
It changes nothing. `src/units/accounts/risk.py`, `config/accounts.yaml` and
`config/strategies.yaml` are read-only here — every sizing change this evidence
argues for is a **Tier-3 proposal for the operator**. It proposes no disposition
for any leg. It does **not** reconstruct the pre-clamp quantity itself: that
needs the decision-time equity, which IS recorded (`account_context_snapshots`)
and has no read surface (`diag.py::_JOURNAL_TABLES` admits four tables and not
that one) — naming that is a finding, not a substitute for the measurement.

⚠️ FUTURES ARE OUT OF SCOPE BY CONSTRUCTION. ``position_size`` skips the crypto
margin cap entirely for ``market_type: futures`` (broker-side SPAN margin), so
the ratio describes no ceiling there and those accounts are refused rather than
graded.

Usage:
  python3 scripts/research/margin_bound_sizing_basis.py --self-test
  python3 scripts/research/margin_bound_sizing_basis.py --journal j.json --packages p.json
  python3 scripts/research/margin_bound_sizing_basis.py --journal j.json --packages p.json --json
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

# The declared-initial-risk reader has ONE owner. Importing it is deliberate:
# re-deriving "what is the decision-time risk distance" is how the second copy
# drifts from the first, and this module's whole argument rests on NOT reading
# `trades.stop_loss` / `order_packages.sl`, both of which the monitor overwrites
# on every trailing amend (`r_provenance`'s module docstring is the record).
from src.runtime.r_provenance import declared_initial_risk  # noqa: E402

# PnL provenance has ONE owner too. The outcome block below refuses to quote a
# dollar figure without grading it through this module first.
from src.runtime import provenance as _prov  # noqa: E402

# The margin safety buffer likewise has one owner. Imported, not copied, so a
# change to the constant moves this instrument with it.
try:
    from src.units.accounts.risk import _MARGIN_SAFETY_BUFFER as MARGIN_BUFFER
except ImportError:  # pragma: no cover — narrow on purpose
    # Narrow, and it FAILS LOUD downstream rather than quietly: with no buffer
    # every ratio and every binding distance returns None, which the renderer
    # prints as UNKNOWN. A default of 0.9 here would let the instrument keep
    # producing numbers after losing its own constant's owner.
    MARGIN_BUFFER = None

# --- qty_provenance: is `position_size` a RiskManager.position_size output? ---
QTY_SIZER = "sizer_output"
QTY_INTENT_REDUCE = "intent_reduce"
QTY_VENUE_ADOPTED = "venue_adopted"
QTY_ISOLATED_PATH = "isolated_order_path"
QTY_UNKNOWN = "unknown"

# --- size_basis: was the size the risk number, or the ceiling? ---
BASIS_PROVABLY_BOUND = "provably_margin_bound"
BASIS_OBSERVED_CEILING = "observed_at_ceiling"
BASIS_RISK_DERIVED = "risk_derived"
BASIS_NOT_PROVABLE = "not_provable"
BASIS_NO_BASIS = "no_basis"

# A pairs leg is named from config/pairs.yaml as "<pair>_a" / "<pair>_b"; the
# prefix is the stable part and is what the journal carries.
_PAIRS_PREFIX = "pairs_"


def _num(v):
    """float(v) or None. Never raises; a bool is not a number here."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def parse_blob(v):
    """A JSON string or an already-decoded mapping -> dict, else {}."""
    if isinstance(v, (str, bytes, bytearray)):
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            return {}
    return v if isinstance(v, dict) else {}


def binding_stop_bp(risk_pct, leverage, buffer_=None):
    """The stop distance, in basis points, BELOW which the margin cap must bind.

    ``10000 x risk_pct / (leverage x buffer)``. Returns None when any input is
    missing or non-positive — never 0.0, which would read as *"every stop
    binds"*.
    """
    r = _num(risk_pct)
    lev = _num(leverage)
    buf = _num(buffer_ if buffer_ is not None else MARGIN_BUFFER)
    if r is None or lev is None or buf is None:
        return None
    if r <= 0 or lev <= 0 or buf <= 0:
        return None
    return 10000.0 * r / (lev * buf)


def margin_bound_ratio(entry, risk_per_unit, risk_pct, leverage, buffer_=None):
    """Demanded notional / ceiling notional, with the equity term cancelled.

    ``risk_pct x entry / (risk_per_unit x leverage x buffer)``. A LOWER BOUND on
    the true ratio, because every margin basis the sizer may use is at most the
    account's equity. ``>= 1`` proves the cap binds at any balance; ``< 1``
    proves NOTHING. Returns None when an input is missing or non-positive.
    """
    e = _num(entry)
    rpu = _num(risk_per_unit)
    r = _num(risk_pct)
    lev = _num(leverage)
    buf = _num(buffer_ if buffer_ is not None else MARGIN_BUFFER)
    if None in (e, rpu, r, lev, buf):
        return None
    if e <= 0 or rpu <= 0 or r <= 0 or lev <= 0 or buf <= 0:
        return None
    return (r * e) / (rpu * lev * buf)


def stop_distance_bp(entry, risk_per_unit):
    """The DECLARED initial stop distance as basis points of entry, or None."""
    e = _num(entry)
    rpu = _num(risk_per_unit)
    if e is None or rpu is None or e <= 0 or rpu <= 0:
        return None
    return 10000.0 * rpu / e


def qty_provenance(row):
    """Which producer wrote this row's ``position_size``. Five states, never
    collapsed.

    ``sizer_output`` is asserted only when nothing contradicts it, and
    ``unknown`` exists so that *"we could not tell"* never renders as a sizer
    output — inverting a non-sizer quantity for a risk number is the defect this
    axis exists to prevent.
    """
    setup = str(row.get("setup_type") or "").strip().lower()
    notes = parse_blob(row.get("notes"))
    strategy = str(row.get("strategy_name") or "").strip().lower()

    if strategy.startswith(_PAIRS_PREFIX):
        return QTY_ISOLATED_PATH
    if setup == "intent_reduce" or "intent_reduce" in notes:
        return QTY_INTENT_REDUCE
    if setup == "adopted_orphan":
        return QTY_VENUE_ADOPTED
    if not setup:
        return QTY_UNKNOWN
    return QTY_SIZER


def stamped_ceiling(row):
    """``notes.margin_basis.max_qty_by_margin``, or None.

    None means the stamp is ABSENT (it ships on refusal rows only, from
    2026-08-13), never that no ceiling applied.
    """
    mb = parse_blob(row.get("notes")).get("margin_basis")
    if not isinstance(mb, dict):
        return None
    return _num(mb.get("max_qty_by_margin"))


def at_stamped_ceiling(row, rel_tol=1e-4):
    """True / False / None — is ``position_size`` the stamped ceiling?

    None is ***we could not look*** (no stamp, or no usable quantity), and is
    deliberately not False.
    """
    cap = stamped_ceiling(row)
    ps = _num(row.get("position_size"))
    if cap is None or ps is None or cap <= 0 or ps <= 0:
        return None
    return abs(ps - cap) / cap < rel_tol


def size_basis(row, entry, risk_per_unit, risk_pct, leverage, buffer_=None):
    """Grade one row. Returns ``(state, reason)``.

    Order of tests is load-bearing: a row that is BOTH provable and observed at
    its stamped ceiling grades ``provably_margin_bound``, because that is the
    stronger statement (it holds at any balance, not only at the one recorded).
    """
    ps = _num(row.get("position_size"))
    ratio = margin_bound_ratio(entry, risk_per_unit, risk_pct, leverage, buffer_)
    if ps is None or ps <= 0:
        return BASIS_NO_BASIS, "no positive position_size"
    if ratio is None:
        return BASIS_NO_BASIS, "entry / risk_per_unit / account terms unusable"
    if ratio >= 1.0:
        return BASIS_PROVABLY_BOUND, f"ratio {ratio:.3f} >= 1 at any balance"
    observed = at_stamped_ceiling(row)
    if observed is True:
        return BASIS_OBSERVED_CEILING, "position_size == stamped max_qty_by_margin"
    if observed is False:
        return BASIS_RISK_DERIVED, "stamped ceiling present and not reached"
    return BASIS_NOT_PROVABLE, f"ratio {ratio:.3f} < 1 and no stamped ceiling"


def _pnl_bucket(row):
    """The canonical PnL provenance bucket for a row. Never re-derived here."""
    c = _prov.classify_pnl(row)
    return c[0] if isinstance(c, (tuple, list)) else c


def outcomes(trades, packages, *, account_id, risk_pct, leverage,
             buffer_=None, prefix="ict_scalp"):
    """Did the margin-bound cohort do BETTER or WORSE? Pooled, and per leg.

    ⚠️ **THE WITHIN-LEG SPLIT IS NOT A REFINEMENT, IT IS THE CONTROL.** A pooled
    bound-vs-unbound comparison is confounded by leg composition: a leg with
    ZERO bound rows contributes its entire PnL to the unbound arm, and on the
    live pull one such leg carried 60% of the unbound loss. Reporting the pooled
    figure alone would have produced a confident finding that the per-leg table
    contradicts. Both are returned, and the renderer prints them together.

    Every dollar figure is reported twice: headline, and restricted to rows
    `src.runtime.provenance` grades MEASURED. No verdict is emitted — this block
    supplies the numbers a Tier-3 decision needs and takes no view.
    """
    pidx = {p.get("order_package_id"): p for p in (packages or [])
            if p.get("order_package_id")}
    rows = []
    for t in trades or []:
        if t.get("account_id") != account_id or t.get("is_backtest"):
            continue
        name = str(t.get("strategy_name") or "")
        if prefix and not name.startswith(prefix):
            continue
        if t.get("status") != "closed" or _num(t.get("pnl")) is None:
            continue
        if qty_provenance(t) != QTY_SIZER:
            continue
        pkg = pidx.get(t.get("order_package_id"))
        if pkg is None:
            continue
        rpu = declared_initial_risk(pkg.get("meta"))
        ratio = margin_bound_ratio(pkg.get("entry"), rpu, risk_pct, leverage, buffer_)
        if ratio is None:
            continue
        rows.append((name, ratio >= 1.0, _num(t.get("pnl")), _pnl_bucket(t),
                     (_num(t.get("position_size")) or 0.0) * rpu))

    def arm(sel):
        pnl = [r[2] for r in sel]
        meas = [r[2] for r in sel if r[3] == _prov.MEASURED]
        risk = sorted(r[4] for r in sel if r[4] > 0)
        return {
            "n": len(sel),
            "pnl": round(sum(pnl), 2) if pnl else None,
            "n_measured": len(meas),
            "pnl_measured": round(sum(meas), 2) if meas else None,
            "pnl_coverage": round(len(meas) / len(sel), 4) if sel else None,
            "wins": sum(1 for v in pnl if v > 0),
            "risk_usd_median": (round(risk[len(risk) // 2], 2) if risk else None),
        }

    legs = sorted({r[0] for r in rows})
    per_leg = {}
    comparable = 0
    for leg in legs:
        b = [r for r in rows if r[0] == leg and r[1]]
        u = [r for r in rows if r[0] == leg and not r[1]]
        per_leg[leg] = {"bound": arm(b), "unbound": arm(u)}
        if len(b) >= 3 and len(u) >= 3:
            comparable += 1
            per_leg[leg]["comparable"] = True
    return {
        "prefix": prefix,
        "pooled": {"bound": arm([r for r in rows if r[1]]),
                   "unbound": arm([r for r in rows if not r[1]])},
        "per_leg": per_leg,
        "legs_with_both_arms_n_ge_3": comparable,
        "legs_with_no_bound_rows": [k for k, v in per_leg.items()
                                    if v["bound"]["n"] == 0],
    }


def report(trades, packages, *, account_id, risk_pct, leverage,
           buffer_=None, market_type="linear", strategy=None):
    """Grade a journal pull. Returns a plain dict (the ``--json`` payload)."""
    buf = _num(buffer_ if buffer_ is not None else MARGIN_BUFFER)
    out = {
        "account_id": account_id,
        "market_type": market_type,
        "risk_pct": risk_pct,
        "leverage": leverage,
        "margin_buffer": buf,
        "binding_stop_bp": binding_stop_bp(risk_pct, leverage, buf),
        "refused": None,
        "population": {},
        "qty_provenance": {},
        "size_basis": {},
        "per_strategy": {},
        "control": {},
        "provable_coverage": None,
    }
    if str(market_type).strip().lower() == "futures":
        # position_size skips the crypto margin cap for futures, so the ratio
        # describes no ceiling. Refuse rather than print a number about a gate
        # that does not run.
        out["refused"] = (
            "market_type=futures: risk.py skips the crypto margin pre-flight cap, "
            "so margin_bound_ratio describes no ceiling on this account"
        )
        return out

    pidx = {p.get("order_package_id"): p for p in (packages or [])
            if p.get("order_package_id")}

    seen = 0
    skipped = collections.Counter()
    graded = []
    for t in trades or []:
        if t.get("account_id") != account_id:
            skipped["other_account"] += 1
            continue
        if t.get("is_backtest"):
            skipped["backtest"] += 1
            continue
        if strategy and t.get("strategy_name") != strategy:
            skipped["other_strategy"] += 1
            continue
        seen += 1
        prov = qty_provenance(t)
        pkg = pidx.get(t.get("order_package_id"))
        if pkg is None:
            skipped["package_absent_from_pull"] += 1
            graded.append((t, prov, None, None, BASIS_NO_BASIS, None, None))
            continue
        rpu = declared_initial_risk(pkg.get("meta"))
        entry = _num(pkg.get("entry"))
        ratio = margin_bound_ratio(entry, rpu, risk_pct, leverage, buf)
        bp = stop_distance_bp(entry, rpu)
        state, _reason = size_basis(t, entry, rpu, risk_pct, leverage, buf)
        graded.append((t, prov, ratio, bp, state, rpu, entry))

    out["population"] = {
        "rows_in_pull": len(trades or []),
        "packages_in_pull": len(pidx),
        "rows_for_this_account": seen,
        "skipped": dict(skipped),
    }
    out["qty_provenance"] = dict(collections.Counter(g[1] for g in graded))

    # The sizing verdict is reported over SIZER-OUTPUT rows only. Grading an
    # intent-reduce or venue-adopted quantity against a risk model would be the
    # exact conflation this module's second axis exists to refuse.
    sizer = [g for g in graded if g[1] == QTY_SIZER]
    out["size_basis"] = dict(collections.Counter(g[4] for g in sizer))
    out["sizer_rows"] = len(sizer)

    gradeable = [g for g in sizer if g[4] != BASIS_NO_BASIS]
    definite = [g for g in gradeable if g[4] != BASIS_NOT_PROVABLE]
    out["provable_coverage"] = (
        round(len(definite) / len(gradeable), 4) if gradeable else None
    )

    # ⚠️ THE PER-STRATEGY TABLE IS COMPUTED OVER THE **GRADEABLE** ROWS — the
    # same population the size_basis census grades — and the two are RECONCILED
    # below rather than trusted. The first cut of this function keyed only on
    # "has a usable ratio", which silently admitted the 113 rows whose
    # position_size is 0.0 (a refusal, or an intent-layer drop): those rows have
    # a signal GEOMETRY but no order SIZE, so counting them made the table
    # report 62 provably-bound orders against a census of 43. A signal that was
    # never sized is not an order whose size was the ceiling.
    per = collections.defaultdict(list)
    geom = collections.defaultdict(lambda: [0, 0])
    for g in sizer:
        if g[2] is None or g[3] is None:
            continue
        name = g[0].get("strategy_name")
        if g[4] == BASIS_NO_BASIS:
            # Geometry without a size: kept and reported SEPARATELY, never
            # dropped (dropping it would shrink a denominator silently) and
            # never pooled with the sized rows.
            geom[name][0] += 1
            geom[name][1] += 1 if g[2] >= 1.0 else 0
            continue
        per[name].append((g[2], g[3]))
    rows = {}
    for name, vals in per.items():
        ratios = sorted(v[0] for v in vals)
        bps = sorted(v[1] for v in vals)
        bound = sum(1 for r in ratios if r >= 1.0)
        rows[name] = {
            "n": len(vals),
            "provably_bound": bound,
            "provably_bound_pct": round(100.0 * bound / len(vals), 1),
            "ratio_median": round(statistics.median(ratios), 4),
            "ratio_max": round(max(ratios), 3),
            "stop_bp_p25": round(bps[min(len(bps) - 1, int(0.25 * len(bps)))], 2),
            "stop_bp_median": round(statistics.median(bps), 2),
            "stop_bp_min": round(min(bps), 2),
        }
    out["per_strategy"] = dict(sorted(
        rows.items(), key=lambda kv: -kv[1]["provably_bound_pct"]))
    out["geometry_only"] = {
        "rows": sum(v[0] for v in geom.values()),
        "would_be_bound": sum(v[1] for v in geom.values()),
        "per_strategy": {k: {"n": v[0], "would_be_bound": v[1]}
                         for k, v in sorted(geom.items(), key=lambda kv: -kv[1][0])},
    }
    # The assertion inside the transform. A table that does not sum to its own
    # census is the defect this field exists to make visible; it is REPORTED
    # rather than raised, because a report that refuses to print says less than
    # one that prints and names its disagreement.
    out["reconciles"] = {
        "per_strategy_n": sum(r["n"] for r in rows.values()),
        "gradeable_rows": len(gradeable),
        "per_strategy_bound": sum(r["provably_bound"] for r in rows.values()),
        "census_provably_bound": out["size_basis"].get(BASIS_PROVABLY_BOUND, 0),
        "ok": (sum(r["n"] for r in rows.values()) == len(gradeable)
               and sum(r["provably_bound"] for r in rows.values())
               == out["size_basis"].get(BASIS_PROVABLY_BOUND, 0)),
    }

    # --- the control: predicted-bound vs the row's OWN stamped outcome -------
    # A FALSE POSITIVE (predicted bound, observed below its stamped ceiling)
    # would falsify the inequality. A false NEGATIVE is expected and is the
    # slack the coverage figure reports.
    tab = collections.Counter()
    false_positive_ids = []
    for t, prov, ratio, _bp, _state, _rpu, _entry in graded:
        if prov != QTY_SIZER or ratio is None:
            continue
        obs = at_stamped_ceiling(t)
        if obs is None:
            continue
        tab[(ratio >= 1.0, obs)] += 1
        if ratio >= 1.0 and obs is False:
            false_positive_ids.append(t.get("id"))
    total = sum(tab.values())
    out["control"] = {
        "stamped_rows": total,
        "predicted_bound_and_at_ceiling": tab[(True, True)],
        "predicted_bound_and_not_at_ceiling": tab[(True, False)],
        "not_predicted_and_at_ceiling": tab[(False, True)],
        "not_predicted_and_not_at_ceiling": tab[(False, False)],
        "false_positive_ids": false_positive_ids,
        "sound": (tab[(True, False)] == 0) if total else None,
    }
    return out


def render(v):
    """Human-readable report. Every rate carries its denominator."""
    L = []
    a = v["account_id"]
    L.append(f"MARGIN-BOUND SIZING BASIS — account {a} (market_type={v['market_type']})")
    if v.get("refused"):
        L.append(f"  REFUSED: {v['refused']}")
        return "\n".join(L)
    L.append(
        f"  declared risk_pct={v['risk_pct']} leverage={v['leverage']} "
        f"buffer={v['margin_buffer']}"
    )
    bb = v["binding_stop_bp"]
    L.append(
        "  binding stop distance = "
        + (f"{bb:.2f} bp — below this the margin cap MUST bind at any balance"
           if bb is not None else "UNKNOWN (account terms unusable)")
    )
    p = v["population"]
    L.append("")
    L.append(f"  POPULATION: {p['rows_in_pull']} journal rows / "
             f"{p['packages_in_pull']} packages in the pull; "
             f"{p['rows_for_this_account']} on this account, non-backtest")
    for k, n in sorted(p["skipped"].items()):
        L.append(f"     skipped {k}: {n}")
    L.append("")
    L.append("  QTY PROVENANCE — who wrote position_size (five states, never collapsed):")
    for k in (QTY_SIZER, QTY_INTENT_REDUCE, QTY_VENUE_ADOPTED,
              QTY_ISOLATED_PATH, QTY_UNKNOWN):
        L.append(f"     {k:22s} {v['qty_provenance'].get(k, 0)}")
    L.append("")
    L.append(f"  SIZE BASIS over the {v.get('sizer_rows', 0)} sizer-output rows:")
    for k in (BASIS_PROVABLY_BOUND, BASIS_OBSERVED_CEILING, BASIS_RISK_DERIVED,
              BASIS_NOT_PROVABLE, BASIS_NO_BASIS):
        L.append(f"     {k:24s} {v['size_basis'].get(k, 0)}")
    pc = v["provable_coverage"]
    L.append(
        "     provableCoverage = "
        + ("n/a (no gradeable rows)" if pc is None else f"{pc:.4f}")
        + "  — the share of gradeable rows on which the equity-free test can"
    )
    L.append("       return a definite answer. `not_provable` is WE DID NOT LOOK,")
    L.append("       never `risk_derived`; quote the bound rate only beside this.")
    L.append("")
    L.append("  PER STRATEGY (sizer-output rows only), sorted by provably-bound share:")
    L.append("     strategy                     n  bound        %   med.ratio  max.ratio"
             "  stop_bp p25 / med / min")
    for name, r in v["per_strategy"].items():
        L.append(
            f"     {str(name):26s} {r['n']:4d} {r['provably_bound']:6d} "
            f"{r['provably_bound_pct']:7.1f}  {r['ratio_median']:9.3f} "
            f"{r['ratio_max']:10.2f}  {r['stop_bp_p25']:9.2f} / "
            f"{r['stop_bp_median']:.2f} / {r['stop_bp_min']:.2f}"
        )
    g = v.get("geometry_only") or {}
    if g.get("rows"):
        L.append("")
        L.append(f"  SIGNAL GEOMETRY WITHOUT AN ORDER SIZE: {g['rows']} rows "
                 f"(position_size 0.0 — a refusal or an intent-layer drop), of which")
        L.append(f"     {g['would_be_bound']} would have been provably margin-bound had they been sized.")
        L.append("     Counted SEPARATELY and never pooled with the table above: a signal that")
        L.append("     was never sized is not an order whose size was the ceiling.")
    rc = v.get("reconciles") or {}
    if rc:
        L.append("")
        L.append("  RECONCILIATION (the assertion inside the transform):")
        L.append(f"     per-strategy n {rc['per_strategy_n']} vs gradeable rows "
                 f"{rc['gradeable_rows']}")
        L.append(f"     per-strategy bound {rc['per_strategy_bound']} vs census "
                 f"provably_margin_bound {rc['census_provably_bound']}")
        L.append("     " + ("OK — the table sums to its own census."
                            if rc.get("ok") else
                            "⚠️ DISAGREES — do not quote either number until this is explained."))
    c = v["control"]
    L.append("")
    L.append("  CONTROL — the equity-free prediction against each row's OWN stamped ceiling")
    L.append(f"     rows carrying notes.margin_basis and a declared risk: {c['stamped_rows']}")
    L.append(f"     predicted bound AND at ceiling      : {c['predicted_bound_and_at_ceiling']}")
    L.append(f"     predicted bound AND NOT at ceiling  : {c['predicted_bound_and_not_at_ceiling']}"
             "   <- FALSE POSITIVES; any is a falsification")
    L.append(f"     not predicted BUT at ceiling        : {c['not_predicted_and_at_ceiling']}"
             "   <- expected slack (equity / basis_usd)")
    L.append(f"     not predicted AND not at ceiling    : {c['not_predicted_and_not_at_ceiling']}")
    if c["sound"] is None:
        L.append("     VERDICT: UNGRADED — no stamped row in this pull. Not a pass.")
    elif c["sound"]:
        L.append("     VERDICT: SOUND on this pull — zero false positives.")
    else:
        L.append(f"     VERDICT: FALSIFIED — ids {c['false_positive_ids']}")
    return "\n".join(L)


def render_outcomes(o):
    """Print the pooled arms and the per-leg control TOGETHER, never apart."""
    L = ["OUTCOME OF THE MARGIN-BOUND COHORT — "
         f"closed sizer-output rows, strategy prefix '{o['prefix']}'",
         "  ⚠️ THE POOLED ROW IS CONFOUNDED BY LEG COMPOSITION. Read it only "
         "beside the per-leg table."]
    def line(tag, a):
        pnl = "n/a" if a["pnl"] is None else f"{a['pnl']:+.2f}"
        pm = "n/a" if a["pnl_measured"] is None else f"{a['pnl_measured']:+.2f}"
        cov = "n/a" if a["pnl_coverage"] is None else f"{a['pnl_coverage']:.4f}"
        rk = "n/a" if a["risk_usd_median"] is None else f"{a['risk_usd_median']:.2f}"
        return (f"     {tag:9s} n={a['n']:4d}  pnl {pnl:>12s}  "
                f"MEASURED-only n={a['n_measured']:3d} {pm:>12s}  "
                f"pnlCoverage {cov}  wins {a['wins']:3d}  median risk_usd {rk:>9s}")
    L.append("")
    L.append("  POOLED:")
    L.append(line("bound", o["pooled"]["bound"]))
    L.append(line("unbound", o["pooled"]["unbound"]))
    L.append("")
    L.append("  PER LEG (the control):")
    for leg, v in o["per_leg"].items():
        mark = "  <- comparable (both arms n>=3)" if v.get("comparable") else ""
        L.append(f"   {leg}{mark}")
        L.append(line("bound", v["bound"]))
        L.append(line("unbound", v["unbound"]))
    L.append("")
    L.append(f"  legs where BOTH arms reach n>=3: {o['legs_with_both_arms_n_ge_3']}")
    if o["legs_with_no_bound_rows"]:
        L.append("  legs contributing ONLY to the unbound arm (pure composition): "
                 + ", ".join(o["legs_with_no_bound_rows"]))
    L.append("  This block emits NO verdict. A pooled difference that reverses "
             "within legs is not evidence about stop width.")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# self-test: planted controls, including defects the instrument must catch
# ---------------------------------------------------------------------------
def _self_test():
    fails = []
    ran = [0]

    def ck(name, cond):
        # The total is COUNTED, never hardcoded: a hardcoded total silently
        # stops matching the moment a control is added or removed, and then
        # "46/46 PASS" is a number about nothing.
        ran[0] += 1
        if not cond:
            fails.append(name)

    # --- the constant is imported, not copied -------------------------------
    ck("margin buffer imported from risk.py", MARGIN_BUFFER is not None)
    ck("margin buffer is the 0.9 the cap uses", MARGIN_BUFFER == 0.9)

    # --- binding_stop_bp ----------------------------------------------------
    ck("binding bp at 1.5%/3x/0.9 is 55.56",
       abs(binding_stop_bp(0.015, 3, 0.9) - 55.5555) < 1e-3)
    ck("binding bp None on zero leverage", binding_stop_bp(0.015, 0, 0.9) is None)
    ck("binding bp None on missing risk_pct", binding_stop_bp(None, 3, 0.9) is None)
    ck("binding bp is not 0.0 when unusable",
       binding_stop_bp(0.0, 3, 0.9) is None)

    # --- margin_bound_ratio: the arithmetic, stated as an identity ----------
    # A stop exactly at the binding distance gives a ratio of exactly 1.
    entry, bp = 100.0, 55.5555555
    rpu = entry * bp / 10000.0
    ck("ratio == 1 at the binding stop distance",
       abs(margin_bound_ratio(entry, rpu, 0.015, 3, 0.9) - 1.0) < 1e-5)
    ck("ratio > 1 inside the binding distance",
       margin_bound_ratio(100.0, 0.0046 * 100, 0.015, 3, 0.9) > 1.0)
    ck("ratio < 1 outside the binding distance",
       margin_bound_ratio(100.0, 0.02 * 100, 0.015, 3, 0.9) < 1.0)
    ck("ratio scale-free in price",
       abs(margin_bound_ratio(100.0, 1.0, 0.015, 3, 0.9)
           - margin_bound_ratio(1000.0, 10.0, 0.015, 3, 0.9)) < 1e-9)
    ck("ratio None on zero risk_per_unit",
       margin_bound_ratio(100.0, 0.0, 0.015, 3, 0.9) is None)
    ck("ratio None on negative entry",
       margin_bound_ratio(-1.0, 1.0, 0.015, 3, 0.9) is None)
    ck("ratio None, never 0.0, on a missing input",
       margin_bound_ratio(100.0, None, 0.015, 3, 0.9) is None)

    # --- stop_distance_bp ---------------------------------------------------
    ck("stop bp 100 on a 1% stop", abs(stop_distance_bp(100.0, 1.0) - 100.0) < 1e-9)
    ck("stop bp None on zero entry", stop_distance_bp(0.0, 1.0) is None)

    # --- qty_provenance: five states, all reachable -------------------------
    ck("sizer output", qty_provenance(
        {"setup_type": "ict_scalp_avax_5m", "strategy_name": "ict_scalp_avax_5m"})
        == QTY_SIZER)
    ck("intent_reduce by setup_type", qty_provenance(
        {"setup_type": "intent_reduce", "strategy_name": "sol_pullback_2h"})
        == QTY_INTENT_REDUCE)
    ck("intent_reduce by notes when setup_type lies", qty_provenance(
        {"setup_type": "sol_pullback_2h", "strategy_name": "sol_pullback_2h",
         "notes": json.dumps({"intent_reduce": True})}) == QTY_INTENT_REDUCE)
    ck("venue adopted", qty_provenance(
        {"setup_type": "adopted_orphan", "strategy_name": "ict_scalp_sol_15m"})
        == QTY_VENUE_ADOPTED)
    ck("pairs sleeve is the isolated path", qty_provenance(
        {"setup_type": "pairs_sol_eth_a", "strategy_name": "pairs_sol_eth_a"})
        == QTY_ISOLATED_PATH)
    ck("pairs wins over a sizer-looking setup_type", qty_provenance(
        {"setup_type": "whatever", "strategy_name": "pairs_bnb_btc_b"})
        == QTY_ISOLATED_PATH)
    ck("empty setup_type is unknown, not sizer", qty_provenance(
        {"setup_type": "", "strategy_name": "ict_scalp_5m"}) == QTY_UNKNOWN)
    ck("missing setup_type is unknown, not sizer",
       qty_provenance({"strategy_name": "ict_scalp_5m"}) == QTY_UNKNOWN)

    # --- the stamped-ceiling reader -----------------------------------------
    stamped = {"position_size": 100.0, "notes": json.dumps(
        {"margin_basis": {"max_qty_by_margin": 100.0}})}
    ck("at ceiling True", at_stamped_ceiling(stamped) is True)
    below = {"position_size": 90.0, "notes": json.dumps(
        {"margin_basis": {"max_qty_by_margin": 100.0}})}
    ck("at ceiling False", at_stamped_ceiling(below) is False)
    ck("no stamp is None, never False",
       at_stamped_ceiling({"position_size": 90.0}) is None)
    ck("stamp present but qty absent is None",
       at_stamped_ceiling({"notes": json.dumps(
           {"margin_basis": {"max_qty_by_margin": 100.0}})}) is None)
    ck("venue-precision equality counts as at-ceiling",
       at_stamped_ceiling({"position_size": 33265.4, "notes": json.dumps(
           {"margin_basis": {"max_qty_by_margin": 33265.434696282246}})}) is True)

    # --- size_basis: every state reachable, and the ordering is deliberate --
    row = {"position_size": 10.0, "setup_type": "x"}
    ck("provably bound", size_basis(
        row, 100.0, 0.0046 * 100, 0.015, 3, 0.9)[0] == BASIS_PROVABLY_BOUND)
    ck("not provable when slack and unstamped", size_basis(
        row, 100.0, 0.02 * 100, 0.015, 3, 0.9)[0] == BASIS_NOT_PROVABLE)
    ck("observed at ceiling when slack but stamped at cap", size_basis(
        stamped, 100.0, 0.02 * 100, 0.015, 3, 0.9)[0] == BASIS_OBSERVED_CEILING)
    ck("risk derived when stamped below cap", size_basis(
        below, 100.0, 0.02 * 100, 0.015, 3, 0.9)[0] == BASIS_RISK_DERIVED)
    ck("no basis on a zero quantity", size_basis(
        {"position_size": 0.0}, 100.0, 1.0, 0.015, 3, 0.9)[0] == BASIS_NO_BASIS)
    ck("no basis on a missing risk_per_unit", size_basis(
        row, 100.0, None, 0.015, 3, 0.9)[0] == BASIS_NO_BASIS)
    # the stronger statement wins when both apply
    ck("provable beats observed", size_basis(
        stamped, 100.0, 0.0046 * 100, 0.015, 3, 0.9)[0] == BASIS_PROVABLY_BOUND)

    # --- report(): futures is refused, not graded ---------------------------
    fut = report([], [], account_id="ib_paper", risk_pct=0.015, leverage=1,
                 market_type="futures")
    ck("futures refused", fut["refused"] is not None)
    ck("futures refusal prints, and grades nothing", fut["size_basis"] == {})

    # --- report(): a PLANTED FALSE POSITIVE must falsify the control --------
    pkgs = [
        {"order_package_id": "p1", "entry": 100.0,
         "meta": json.dumps({"risk_per_unit": 0.046})},   # 4.6 bp -> ratio ~12
        {"order_package_id": "p2", "entry": 100.0,
         "meta": json.dumps({"risk_per_unit": 2.0})},     # 200 bp -> ratio ~0.28
    ]
    # p1 is predicted bound; its stamp says it did NOT reach the ceiling.
    bad = [
        {"id": 1, "account_id": "a", "setup_type": "s", "strategy_name": "s",
         "order_package_id": "p1", "position_size": 50.0,
         "notes": json.dumps({"margin_basis": {"max_qty_by_margin": 100.0}})},
    ]
    r_bad = report(bad, pkgs, account_id="a", risk_pct=0.015, leverage=3,
                   buffer_=0.9)
    ck("planted false positive is caught", r_bad["control"]["sound"] is False)
    ck("planted false positive is named", r_bad["control"]["false_positive_ids"] == [1])

    good = [
        {"id": 2, "account_id": "a", "setup_type": "s", "strategy_name": "s",
         "order_package_id": "p1", "position_size": 100.0,
         "notes": json.dumps({"margin_basis": {"max_qty_by_margin": 100.0}})},
        {"id": 3, "account_id": "a", "setup_type": "s", "strategy_name": "s",
         "order_package_id": "p2", "position_size": 100.0,
         "notes": json.dumps({"margin_basis": {"max_qty_by_margin": 100.0}})},
    ]
    r_good = report(good, pkgs, account_id="a", risk_pct=0.015, leverage=3,
                    buffer_=0.9)
    ck("clean control is sound", r_good["control"]["sound"] is True)
    ck("the expected slack is counted, not hidden",
       r_good["control"]["not_predicted_and_at_ceiling"] == 1)

    # --- report(): an EMPTY control is UNGRADED, never a pass ---------------
    r_empty = report(
        [{"id": 4, "account_id": "a", "setup_type": "s", "strategy_name": "s",
          "order_package_id": "p2", "position_size": 10.0}],
        pkgs, account_id="a", risk_pct=0.015, leverage=3, buffer_=0.9)
    ck("no stamped row -> control UNGRADED", r_empty["control"]["sound"] is None)
    ck("an unstamped slack row is not_provable",
       r_empty["size_basis"].get(BASIS_NOT_PROVABLE) == 1)
    ck("provableCoverage 0.0 when nothing is definite",
       r_empty["provable_coverage"] == 0.0)

    # --- report(): a non-sizer quantity must never reach the sizing verdict -
    mixed = [
        {"id": 5, "account_id": "a", "setup_type": "intent_reduce",
         "strategy_name": "s", "order_package_id": "p1", "position_size": 99.0},
        {"id": 6, "account_id": "a", "setup_type": "adopted_orphan",
         "strategy_name": "s", "order_package_id": "p1", "position_size": 99.0},
        {"id": 7, "account_id": "a", "setup_type": "s",
         "strategy_name": "pairs_sol_eth_a", "order_package_id": "p1",
         "position_size": 99.0},
    ]
    r_mixed = report(mixed, pkgs, account_id="a", risk_pct=0.015, leverage=3,
                     buffer_=0.9)
    ck("no sizer rows among the three non-sizer populations",
       r_mixed["sizer_rows"] == 0)
    ck("the sizing verdict is empty, not a bound rate",
       r_mixed["size_basis"] == {})
    ck("provableCoverage is None, not 0.0, with nothing gradeable",
       r_mixed["provable_coverage"] is None)
    ck("each non-sizer producer is counted separately",
       r_mixed["qty_provenance"] == {QTY_INTENT_REDUCE: 1, QTY_VENUE_ADOPTED: 1,
                                     QTY_ISOLATED_PATH: 1})

    # --- report(): a row whose package is absent is NO_BASIS, not a pass ----
    r_nopkg = report(
        [{"id": 8, "account_id": "a", "setup_type": "s", "strategy_name": "s",
          "order_package_id": "missing", "position_size": 10.0}],
        pkgs, account_id="a", risk_pct=0.015, leverage=3, buffer_=0.9)
    ck("absent package -> no_basis",
       r_nopkg["size_basis"].get(BASIS_NO_BASIS) == 1)
    ck("absent package is counted in skipped",
       r_nopkg["population"]["skipped"].get("package_absent_from_pull") == 1)

    # --- render() never raises on any of the above --------------------------
    for r in (fut, r_bad, r_good, r_empty, r_mixed, r_nopkg):
        try:
            render(r)
        except Exception as exc:  # noqa: BLE001  # allow-silent: this IS the assertion — the control is that render() raises for NO reachable report, so the broad catch is the test's subject and every catch becomes a named FAIL rather than a swallowed error.
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
                return d[k]
    return d if isinstance(d, list) else []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal", help="a /api/diag/journal?table=trades pull (JSON)")
    ap.add_argument("--packages",
                    help="a /api/diag/journal?table=order_packages pull (JSON)")
    ap.add_argument("--account", default="bybit_1")
    ap.add_argument("--strategy", default=None,
                    help="restrict to one strategy_name")
    ap.add_argument("--risk-pct", type=float, default=None,
                    help="override; default reads config/accounts.yaml")
    ap.add_argument("--leverage", type=float, default=None,
                    help="override; default reads config/accounts.yaml")
    ap.add_argument("--outcomes", action="store_true",
                    help="also grade the bound cohort's PnL, pooled AND per leg")
    ap.add_argument("--prefix", default="ict_scalp",
                    help="strategy-name prefix for --outcomes")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()
    if not a.journal or not a.packages:
        ap.error("--journal and --packages are both required "
                 "(the declared initial risk lives on the package)")

    risk_pct, leverage, market_type = a.risk_pct, a.leverage, "linear"
    if risk_pct is None or leverage is None:
        # The ONE canonical reader for accounts.yaml. A hand-rolled
        # yaml.safe_load here would be a second parser free to disagree with the
        # one the trader uses about what this account declares — and the whole
        # measurement is keyed on risk_pct and leverage.
        try:
            from src.config.accounts_loader import load_accounts_dict
            acct = dict((load_accounts_dict() or {}).get(a.account) or {})
        except (ImportError, OSError, ValueError) as exc:
            # Narrow, and it REFUSES rather than defaulting: guessing 1.5% / 3x
            # would print a binding distance for terms this account may not
            # declare, which is the unprovenanced-diagnostic class exactly.
            print(f"could not read accounts.yaml through the canonical loader "
                  f"({type(exc).__name__}: {exc}); pass --risk-pct / --leverage",
                  file=sys.stderr)
            return 2
        rk = acct.get("risk") or {}
        risk_pct = risk_pct if risk_pct is not None else rk.get("risk_pct")
        leverage = leverage if leverage is not None else rk.get("leverage")
        market_type = str(acct.get("market_type") or "linear")
        if risk_pct is None or leverage is None:
            print(f"account {a.account!r} declares no risk.risk_pct / "
                  "risk.leverage; pass --risk-pct / --leverage", file=sys.stderr)
            return 2

    v = report(_load(a.journal), _load(a.packages), account_id=a.account,
               risk_pct=risk_pct, leverage=leverage, market_type=market_type,
               strategy=a.strategy)
    if a.outcomes:
        v["outcomes"] = outcomes(
            _load(a.journal), _load(a.packages), account_id=a.account,
            risk_pct=risk_pct, leverage=leverage, prefix=a.prefix)
    if a.json:
        print(json.dumps(v, indent=2))
    else:
        print(render(v))
        if a.outcomes:
            print()
            print(render_outcomes(v["outcomes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
