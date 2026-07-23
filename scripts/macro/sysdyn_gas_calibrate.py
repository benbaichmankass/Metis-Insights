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
    EIA_STORAGE_SERIES,
    HENRY_HUB_WEEKLY_SERIES,
    build_calibration_series,
    build_dual_calibration_series,
    fetch_eia_storage_dated,
    fetch_weekly_ng_price_dated,
    national_daily_hdd,
)

from src.sysdyn.engine import simulate  # noqa: E402
from src.sysdyn.identify import identify, r_squared, rmse, walk_forward_stability  # noqa: E402
from src.sysdyn.seed_gas import (  # noqa: E402
    DEFAULT_PARAMS,
    FREE_PARAM_BOUNDS,
    build_gas_storage_model,
    price_series,
    storage_series,
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


def _f(x):
    """Round a float to 6dp for the JSON scorecard; ``None`` for None/NaN (honest-null)."""
    return None if x is None or (isinstance(x, float) and x != x) else round(float(x), 6)


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


DEFAULT_DUAL_SCORECARD_PATH = os.path.join("comms", "macro", "sysdyn_gas_dual_scorecard.json")


def _print_price_card(card: dict) -> None:
    print("M29 P1b — gas_storage_price_v1 calibration scorecard (price-only)")
    print("=" * 52)
    if card.get("error"):
        print(f"ERROR: {card['error']}  (span {card['span'][0]} … {card['span'][1]}, n={card['n_obs']})")
        return
    th, st, vd = card["train_holdout"], card["stability"], card["verdict"]
    print(f"data     : {card['n_obs']} weekly obs  {card['span'][0]} … {card['span'][1]}")
    print(f"OOS      : rmse={th['oos_rmse']}  r2={th['oos_r2']}  (holdout {th['holdout_n']} of {card['n_obs']})")
    print(f"stability: structural_max_rel_spread={st['structural_max_rel_spread']} (thr {st['stability_threshold']})")
    print(f"params   : {th['params']}")
    print(f"verdict  : {vd['label']}  identifiable={vd['identifiable']} oos_edge={vd['explains_oos_seasonality']}")


def _print_dual_card(card: dict) -> None:
    print("M29 P1c — gas_storage_price_v1 DUAL-TARGET calibration (storage + weather HDD + price)")
    print("=" * 52)
    if card.get("error"):
        print(f"ERROR: {card['error']}  (span {card['span'][0]} … {card['span'][1]}, n={card['n_obs']})")
        return
    th, st, vd = card["train_holdout"], card["stability"], card["verdict"]
    sf, pf = th["storage_fit"], th["price_fit"]
    print(f"data     : {card['n_obs']} weekly obs  {card['span'][0]} … {card['span'][1]}  "
          f"(storage={card['n_storage_obs']} price={card['n_price_obs']} hdd_days={card['n_hdd_days']})")
    print(f"storage  : in_r2={sf['in_sample_r2']}  OOS_r2={sf['oos_r2']}  (anchor target)")
    print(f"price    : in_r2={pf['in_sample_r2']}  OOS_r2={pf['oos_r2']}  (downstream readout — the tradeable quantity)")
    print(f"stability: structural_max_rel_spread={st['structural_max_rel_spread']} (thr {st['stability_threshold']})")
    print(f"params   : {th['params']}")
    print(f"verdict  : {vd['label']}  identifiable={vd['identifiable']} "
          f"storage_oos={vd['storage_tracks_oos']} price_oos={vd['price_readout_predicts_oos']}")
    print(f"GO/NO-GO : {vd['go_no_go']}")


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="M29 P1b/P1c — calibrate gas_storage_price_v1 on real data")
    ap.add_argument("--mode", choices=["price", "dual"], default="price",
                    help="price = P1b (price-only, calendar demand); dual = P1c (storage anchor + weather HDD + price)")
    ap.add_argument("--series", default=HENRY_HUB_WEEKLY_SERIES, help="FRED weekly price series id")
    ap.add_argument("--storage-series", default=EIA_STORAGE_SERIES, help="EIA v2 weekly storage series id (dual mode)")
    ap.add_argument("--window-years", type=float, default=8.0, help="last N years of history (0/blank = all)")
    ap.add_argument("--holdout-frac", type=float, default=0.25, help="tail fraction held out for OOS scoring")
    ap.add_argument("--folds", type=int, default=4, help="walk-forward stability folds")
    ap.add_argument("--path", default=None,
                    help="scorecard JSON out (default: sysdyn_gas_scorecard.json / sysdyn_gas_dual_scorecard.json)")
    ap.add_argument("--generated-at", default=None, help="stamp the scorecard with this ISO timestamp")
    ap.add_argument("--dry-run", action="store_true", help="compute + print; write nothing")
    args = ap.parse_args(argv)

    wy = args.window_years if args.window_years and args.window_years > 0 else None
    if args.mode == "dual":
        card = run_dual_calibration(
            window_years=wy, holdout_frac=args.holdout_frac, n_folds=args.folds,
            storage_series_id=args.storage_series, price_series_id=args.series,
            generated_at=args.generated_at,
        )
        _print_dual_card(card)
        out_path = args.path or DEFAULT_DUAL_SCORECARD_PATH
    else:
        card = run_calibration(
            window_years=wy, holdout_frac=args.holdout_frac, n_folds=args.folds,
            series=args.series, generated_at=args.generated_at,
        )
        _print_price_card(card)
        out_path = args.path or DEFAULT_SCORECARD_PATH

    if not args.dry_run:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
        print(f"wrote {out_path}")
    return 1 if card.get("error") else 0


# ===========================================================================
# M29 P1c — DUAL-TARGET calibration: observed EIA storage (anchor) + real weather
# HDD demand + price readout. The fair test of the SD thesis (the seed finally has
# the inputs its structure was built for), and the mechanistic-vs-static go/no-go.
# ===========================================================================

# The mechanistic edge the go/no-go turns on: with storage anchored to reality and
# the demand driver a real weather series, does the model's PRICE readout track real
# price OUT-OF-SAMPLE? P1b (price-only, calendar demand) got price OOS R²≈0; if the
# storage-anchored + weather-driven model beats that AND is identifiable, the
# mechanism adds signal over the static M28 value read → invest deeper in M29.
DUAL_MIN_OBS = 30  # need a couple of heating seasons for storage/demand to move.


def _span_dates(*dated_lists):
    """Overall [min, max] YYYY-MM-DD across one or more dated [(date,val)] lists."""
    days = [d for lst in dated_lists for d, _ in (lst or [])]
    if not days:
        return None, None
    return min(days), max(days)


def run_dual_calibration(
    dated_storage=None,
    dated_price=None,
    daily_hdd=None,
    *,
    urlopen=None,
    eia_urlopen=None,
    weather_urlopen=None,
    eia_api_key=None,
    storage_series_id: str = EIA_STORAGE_SERIES,
    price_series_id: str = HENRY_HUB_WEEKLY_SERIES,
    window_years: Optional[float] = 8.0,
    holdout_frac: float = 0.25,
    n_folds: int = 4,
    free_base_price: bool = True,
    timeout: float = 25.0,
    generated_at: Optional[str] = None,
) -> dict:
    """Calibrate the seed against observed EIA storage (the anchor 2nd target) +
    real weather HDD demand, and score both the storage fit AND the downstream
    price readout. Never raises on thin/absent data — returns an ``error`` envelope.

    Injected inputs (tests): ``dated_storage`` / ``dated_price`` = ``[(date,val)]``;
    ``daily_hdd`` = national daily HDD ``[(date,hdd)]``. Absent, the off-VM readers
    fetch them (EIA needs ``eia_api_key`` / ``EIA_API_KEY``; weather is keyless)."""
    eia_open = eia_urlopen or urlopen
    wx_open = weather_urlopen or urlopen

    if dated_price is None:
        dated_price = fetch_weekly_ng_price_dated(series=price_series_id, urlopen=urlopen, timeout=timeout)
    if dated_storage is None:
        dated_storage = fetch_eia_storage_dated(
            series=storage_series_id, api_key=eia_api_key, urlopen=eia_open, timeout=timeout
        )
    if daily_hdd is None:
        lo, hi = _span_dates(dated_storage, dated_price)
        if lo and hi:
            if window_years and window_years > 0:
                import datetime as _d
                lo = (_d.date.fromisoformat(hi) - _d.timedelta(days=int(round(window_years * 365.25)))).isoformat()
            daily_hdd = national_daily_hdd(lo, hi, urlopen=wx_open, timeout=timeout)
        else:
            daily_hdd = []

    dates, obs_storage, obs_price, exog, dmeta = build_dual_calibration_series(
        dated_storage, dated_price, daily_hdd, window_years=window_years,
    )
    n = len(dates)

    base = {
        "model": "gas_storage_price_v1",
        "mode": "dual_target",
        "targets": {
            "storage": f"eia_weekly_working_gas_{storage_series_id}",
            "price": f"weekly_henry_hub_price_{price_series_id}",
        },
        "demand_driver": "weather_hdd_open_meteo (real)",
        "target_source": "eia_v2 + fred + open-meteo",
        "generated_at": generated_at,
        "window_years": window_years,
        "n_obs": n,
        "span": dmeta.get("span", [None, None]),
        "n_storage_obs": len(dated_storage or []),
        "n_price_obs": len(dated_price or []),
        "n_hdd_days": len(daily_hdd or []),
    }
    min_needed = max(n_folds, DUAL_MIN_OBS)
    if n < min_needed:
        base["error"] = (
            f"insufficient_history (n={n} < {min_needed}; "
            f"storage={len(dated_storage or [])} price={len(dated_price or [])} hdd={len(daily_hdd or [])})"
        )
        return base

    initial_storage = dmeta["initial_storage"]
    storage_normal = dmeta["storage_normal"]
    model = build_gas_storage_model(initial_storage=initial_storage)
    fixed = {"storage_normal": storage_normal}

    bounds = {k: FREE_PARAM_BOUNDS[k] for k in STRUCTURAL_FREE_PARAMS}
    init = {k: DEFAULT_PARAMS[k] for k in STRUCTURAL_FREE_PARAMS}
    if free_base_price:
        bounds["base_price"] = BASE_PRICE_BOUNDS
        init["base_price"] = _clamp(_median(obs_price), *BASE_PRICE_BOUNDS)
    else:
        fixed["base_price"] = DEFAULT_PARAMS["base_price"]

    holdout_n = max(1, int(round(n * _clamp(holdout_frac, 0.05, 0.5))))
    train_n = n - holdout_n

    # --- joint fit on the TRAIN weeks: stacked, mean-normalised storage + price ---
    ms = sum(obs_storage[:train_n]) / train_n
    mp = sum(obs_price[:train_n]) / train_n
    w_s = 1.0 / ms if ms else 1.0
    w_p = 1.0 / mp if mp else 1.0

    def _stacked_predict(traj):
        s = list(storage_series(traj))
        p = list(price_series(traj))
        return [x * w_s for x in s] + [x * w_p for x in p]

    stacked_obs_train = (
        [x * w_s for x in obs_storage[:train_n]] + [x * w_p for x in obs_price[:train_n]]
    )
    fit = identify(
        model, bounds=bounds, init=init, fixed=fixed, exog=exog[:train_n],
        observed=stacked_obs_train, predict=_stacked_predict, dt=1.0, steps=train_n,
    )

    # --- per-target scoring: simulate the full run once, slice train vs holdout ---
    full = simulate(model, {**fixed, **fit.params}, exog, n, dt=1.0)
    sim_storage = list(storage_series(full))
    sim_price = list(price_series(full))

    def _band(sim, obs):
        return {
            "in_sample_rmse": _f(rmse(sim[:train_n], obs[:train_n])),
            "in_sample_r2": _f(r_squared(sim[:train_n], obs[:train_n])),
            "oos_rmse": _f(rmse(sim[train_n:], obs[train_n:])),
            "oos_r2": _f(r_squared(sim[train_n:], obs[train_n:])),
        }

    storage_fit = _band(sim_storage, obs_storage)
    price_fit = _band(sim_price, obs_price)

    # --- identifiability: walk-forward on the STORAGE target (structural params) ---
    stab = walk_forward_stability(
        model, bounds=bounds, init=init, fixed=fixed, exog=exog, observed=obs_storage,
        predict=storage_series, n_folds=n_folds, dt=1.0,
    )
    structural_spreads = {k: stab.param_rel_spread[k] for k in STRUCTURAL_FREE_PARAMS}
    structural_max_spread = max(structural_spreads.values()) if structural_spreads else None

    identifiable = structural_max_spread is not None and structural_max_spread < STABILITY_THRESHOLD
    price_oos = price_fit["oos_r2"]
    storage_oos = storage_fit["oos_r2"]
    price_oos_edge = price_oos is not None and price_oos == price_oos and price_oos > OOS_EDGE_R2_MIN
    storage_tracks = storage_oos is not None and storage_oos == storage_oos and storage_oos > OOS_EDGE_R2_MIN

    # The decisive go/no-go: the mechanistic model earns deeper M29 investment only
    # if its PRICE readout predicts OOS (beats P1b's ~0) AND its structure is
    # identifiable from real data. Storage tracking alone is necessary, not sufficient.
    invest = bool(price_oos_edge and identifiable)
    go_no_go = "invest_deeper" if invest else "park_deeper_investment"

    base.update({
        "free_params": list(bounds),
        "fixed_params": {k: _f(v) for k, v in fixed.items()},
        "initial_storage": _f(initial_storage),
        "storage_normal": _f(storage_normal),
        "train_holdout": {
            "holdout_frac": holdout_frac,
            "train_n": train_n,
            "holdout_n": holdout_n,
            "storage_fit": storage_fit,
            "price_fit": price_fit,
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
            "storage_tracks_oos": storage_tracks,
            "price_readout_predicts_oos": price_oos_edge,
            "go_no_go": go_no_go,
            "label": _dual_verdict_label(identifiable, storage_tracks, price_oos_edge),
            "notes": (
                "DUAL-TARGET (P1c): storage anchored to observed EIA weekly working-gas "
                "(initial + normal from the real series), demand driven by real weather HDD "
                "(Open-Meteo national basket), price a downstream readout of the storage gap. "
                "The go/no-go turns on whether the PRICE readout predicts OUT-OF-SAMPLE "
                "(P1b price-only got ~0) AND the structural params are identifiable. Storage "
                "tracking OOS is necessary but not sufficient — a well-fit stock trajectory "
                "with no price edge does not beat the static M28 value read."
            ),
        },
        "caveats": [
            "storage is the anchor target (initial_storage + storage_normal from the real series)",
            "demand = real weather HDD (Open-Meteo city basket, gas-heating-weighted); injection_season stays calendar",
            "joint fit is mean-normalised storage+price stacked SSE so neither unit dominates",
            "identifiability judged on the STORAGE-target walk-forward (structural params)",
            "EIA fredgraph/v2 + FRED price are latest-revision; NG price is unrevised → PIT-clean",
        ],
    })
    return base


def _dual_verdict_label(identifiable: bool, storage_tracks: bool, price_predicts: bool) -> str:
    if identifiable and price_predicts:
        return "mechanistic_edge"                     # invest deeper in M29
    if storage_tracks and not price_predicts:
        return "storage_fits_no_price_edge"           # stock well-modelled, no tradeable readout
    if price_predicts and not identifiable:
        return "price_edge_but_equifinal"             # promising but not identifiable
    return "no_mechanistic_edge"                      # park deeper M29 investment


if __name__ == "__main__":
    raise SystemExit(main())
