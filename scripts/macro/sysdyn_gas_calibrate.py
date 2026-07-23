#!/usr/bin/env python3
"""M29 P1b — calibrate the ``gas_storage_price_v1`` seed on REAL natural-gas price
history and emit a scorecard (the system-dynamics analogue of the M28 value gate).

This is the "calibrate the seed on real data via an injected reader" step the seed
model's docstring flags as the remaining P1 work. It runs the pure
`src.sysdyn.identify` harness against the real weekly Henry Hub price
(`sysdyn_gas_data.fetch_weekly_ng_price_dated`) and reports two honest verdicts the
design demands:

  1. **Out-of-sample fit** — fit the free params on the head of the window, then
     score the held-out tail (does the calibrated model track price it never saw?).
  2. **Identifiability / stability** — `walk_forward_stability` fits each fold
     independently and reports how far the recovered STRUCTURAL params move
     fold-to-fold. A small spread ⇒ the structure is identifiable from real data; a
     large one ⇒ equifinality (the design's stop condition — simplify, don't ship).

**Scope honesty (carried from the data adapter):** the target is the keyless real
**price** with a *calendar-seasonal* demand proxy, so the seed can only explain the
**seasonal** component of NG price — the secular level + weather-*surprise* shocks
are out of reach until P1c injects observed EIA storage + weather HDD. The
scorecard states this plainly rather than dressing up a low OOS R² as a failure;
the point of P1b is to prove the harness end-to-end on real data and measure
whether the structure is even identifiable.

The ``base_price`` reference is fit per-run/-fold (the local price anchor) so a
non-stationary price level doesn't strand the seasonal fit; it is **excluded from
the identifiability verdict** (its spread across decades is expected and is not a
structural claim). Pure calibration math (only the fetch is off-VM); injectable
for tests. No order path, no DB write.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sysdyn_gas_data import (  # noqa: E402
    HENRY_HUB_WEEKLY_SERIES,
    build_calibration_series,
    fetch_weekly_ng_price_dated,
)

from src.sysdyn.engine import simulate  # noqa: E402
from src.sysdyn.identify import identify, r_squared, rmse, walk_forward_stability  # noqa: E402
from src.sysdyn.seed_gas import (  # noqa: E402
    DEFAULT_PARAMS,
    FREE_PARAM_BOUNDS,
    build_gas_storage_model,
    price_series,
)

DEFAULT_SCORECARD_PATH = os.path.join("comms", "macro", "sysdyn_gas_scorecard.json")

# The four free params that make a STRUCTURAL claim — the identifiability verdict
# is judged on these (base_price, when freed, is a local level anchor, not structure).
STRUCTURAL_FREE_PARAMS = ("inj_rate", "wd_rate", "price_k", "price_feedback")
BASE_PRICE_BOUNDS = (1.0, 12.0)  # $/MMBtu — spans the real Henry Hub range
# Real data is noisier than the synthetic round-trip (max_rel_spread < 0.05 there);
# a structural param that stays within ~half its own mean across folds is "identifiable".
STABILITY_THRESHOLD = 0.5
# Minimum out-of-sample R² to count as a real edge — a negligibly-positive R²
# (e.g. 0.001) is NOT an edge and must not be dressed as one.
OOS_EDGE_R2_MIN = 0.05


def _median(xs) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _clamp(v, lo, hi):
    return min(max(v, lo), hi)


def run_calibration(
    dated_price=None,
    *,
    urlopen=None,
    series: str = HENRY_HUB_WEEKLY_SERIES,
    window_years: Optional[float] = 8.0,
    holdout_frac: float = 0.25,
    n_folds: int = 4,
    free_base_price: bool = True,
    timeout: float = 25.0,
    generated_at: Optional[str] = None,
) -> dict:
    """Fetch (or accept injected) weekly price, calibrate the seed, return a scorecard
    dict. Never raises on thin/absent data — returns an ``error`` envelope instead."""
    if dated_price is None:
        dated_price = fetch_weekly_ng_price_dated(series=series, urlopen=urlopen, timeout=timeout)
    dates, observed, exog = build_calibration_series(dated_price, window_years=window_years)
    n = len(observed)

    base = {
        "model": "gas_storage_price_v1",
        "target": f"weekly_henry_hub_price_{series}",
        "target_source": "fred",
        "exog": "calendar_seasonal(heating_demand,injection_season by ISO week); "
                "observed EIA storage + weather HDD are P1c",
        "generated_at": generated_at,
        "window_years": window_years,
        "n_obs": n,
        "span": [dates[0], dates[-1]] if dates else [None, None],
    }
    min_needed = max(n_folds, 8)
    if n < min_needed:
        base["error"] = f"insufficient_history (n={n} < {min_needed})"
        return base

    model = build_gas_storage_model(initial_storage=DEFAULT_PARAMS["storage_normal"])

    # Free set: the four structural params, plus base_price (local level anchor).
    bounds = {k: FREE_PARAM_BOUNDS[k] for k in STRUCTURAL_FREE_PARAMS}
    init = {k: DEFAULT_PARAMS[k] for k in STRUCTURAL_FREE_PARAMS}
    fixed = {"storage_normal": DEFAULT_PARAMS["storage_normal"]}
    if free_base_price:
        bounds["base_price"] = BASE_PRICE_BOUNDS
        init["base_price"] = _clamp(_median(observed), *BASE_PRICE_BOUNDS)
    else:
        fixed["base_price"] = DEFAULT_PARAMS["base_price"]

    # --- 1) train / holdout out-of-sample fit ---
    holdout_n = max(1, int(round(n * _clamp(holdout_frac, 0.05, 0.5))))
    train_n = n - holdout_n
    fit = identify(
        model, bounds=bounds, init=init, fixed=fixed, exog=exog, observed=observed,
        predict=price_series, dt=1.0, score_slice=(0, train_n),
    )
    full = list(price_series(simulate(model, {**fixed, **fit.params}, exog, n, dt=1.0)))
    in_rmse = rmse(full[:train_n], observed[:train_n])
    in_r2 = r_squared(full[:train_n], observed[:train_n])
    oos_rmse = rmse(full[train_n:], observed[train_n:])
    oos_r2 = r_squared(full[train_n:], observed[train_n:])

    # --- 2) walk-forward identifiability / stability ---
    stab = walk_forward_stability(
        model, bounds=bounds, init=init, fixed=fixed, exog=exog, observed=observed,
        predict=price_series, n_folds=n_folds, dt=1.0,
    )
    structural_spreads = {k: stab.param_rel_spread[k] for k in STRUCTURAL_FREE_PARAMS}
    structural_max_spread = max(structural_spreads.values()) if structural_spreads else None

    def _f(x):
        return None if x is None or (isinstance(x, float) and x != x) else round(float(x), 6)

    identifiable = structural_max_spread is not None and structural_max_spread < STABILITY_THRESHOLD
    explains_oos = oos_r2 is not None and oos_r2 == oos_r2 and oos_r2 > OOS_EDGE_R2_MIN

    base.update({
        "free_params": list(bounds),
        "fixed_params": {k: _f(v) for k, v in fixed.items()},
        "train_holdout": {
            "holdout_frac": holdout_frac,
            "train_n": train_n,
            "holdout_n": holdout_n,
            "in_sample_rmse": _f(in_rmse),
            "in_sample_r2": _f(in_r2),
            "oos_rmse": _f(oos_rmse),
            "oos_r2": _f(oos_r2),
            "params": {k: _f(v) for k, v in fit.params.items()},
            "converged": fit.converged,
        },
        "stability": {
            "n_folds": n_folds,
            "structural_max_rel_spread": _f(structural_max_spread),
            "stability_threshold": STABILITY_THRESHOLD,
            "oos_edge_r2_min": OOS_EDGE_R2_MIN,
            "param_rel_spread": {k: _f(v) for k, v in stab.param_rel_spread.items()},
            "param_means": {k: _f(v) for k, v in stab.param_means.items()},
        },
        "verdict": {
            "identifiable": identifiable,
            "explains_oos_seasonality": explains_oos,
            "label": _verdict_label(identifiable, explains_oos),
            "notes": (
                "Calendar-seasonal demand proxy against real weekly price: the seed "
                "can explain the SEASONAL component only. A low OOS R² is expected and "
                "is NOT a harness failure — the secular level + weather-surprise shocks "
                "need observed EIA storage + weather HDD (P1c). The load-bearing result "
                "here is whether the structural params are identifiable from real data."
            ),
        },
        "caveats": [
            "target is price (keyless) not observed EIA storage — storage/weather dual-target is P1c",
            "base_price fit per-run/-fold as a local level anchor; excluded from the identifiability verdict",
            "demand amplitude is arbitrary (absorbed by wd_rate); only the calendar season shape is injected",
            "FRED fredgraph latest-revision values; NG price is a market rate (unrevised) → PIT-clean",
        ],
    })
    return base


def _verdict_label(identifiable: bool, explains_oos: bool) -> str:
    if identifiable and explains_oos:
        return "identifiable_seasonal_edge"
    if identifiable:
        return "identifiable_no_oos_edge"
    if explains_oos:
        return "oos_edge_but_equifinal"
    return "equifinal_no_edge"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="M29 P1b — calibrate gas_storage_price_v1 on real NG price")
    ap.add_argument("--series", default=HENRY_HUB_WEEKLY_SERIES, help="FRED weekly price series id")
    ap.add_argument("--window-years", type=float, default=8.0, help="last N years of history (0/blank = all)")
    ap.add_argument("--holdout-frac", type=float, default=0.25, help="tail fraction held out for OOS scoring")
    ap.add_argument("--folds", type=int, default=4, help="walk-forward stability folds")
    ap.add_argument("--path", default=DEFAULT_SCORECARD_PATH, help=f"scorecard JSON out (default {DEFAULT_SCORECARD_PATH})")
    ap.add_argument("--generated-at", default=None, help="stamp the scorecard with this ISO timestamp")
    ap.add_argument("--dry-run", action="store_true", help="compute + print; write nothing")
    args = ap.parse_args(argv)

    wy = args.window_years if args.window_years and args.window_years > 0 else None
    card = run_calibration(
        window_years=wy, holdout_frac=args.holdout_frac, n_folds=args.folds,
        series=args.series, generated_at=args.generated_at,
    )

    print("M29 P1b — gas_storage_price_v1 calibration scorecard")
    print("=" * 52)
    if card.get("error"):
        print(f"ERROR: {card['error']}  (span {card['span'][0]} … {card['span'][1]}, n={card['n_obs']})")
        # Still write the envelope so the workflow surfaces the thin-data state.
    else:
        th = card["train_holdout"]
        st = card["stability"]
        vd = card["verdict"]
        print(f"data     : {card['n_obs']} weekly obs  {card['span'][0]} … {card['span'][1]}")
        print(f"OOS      : rmse={th['oos_rmse']}  r2={th['oos_r2']}  (holdout {th['holdout_n']} of {card['n_obs']})")
        print(f"stability: structural_max_rel_spread={st['structural_max_rel_spread']} (thr {st['stability_threshold']})")
        print(f"params   : {th['params']}")
        print(f"verdict  : {vd['label']}  identifiable={vd['identifiable']} oos_edge={vd['explains_oos_seasonality']}")

    if not args.dry_run:
        p = Path(args.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.path}")
    return 1 if card.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
