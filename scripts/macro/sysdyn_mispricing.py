"""M28 Phase B / M29 bridge — the sysdyn MISPRICING signal.

The M29 gas system-dynamics model was graded on *calibration R²* (does the model
fit/forecast price) and parked (`no_mechanistic_edge`). That answered a different
question than the one that matters for a signal: **does the model-implied
mispricing trade through the same S2+S3 gate as every other construction?**

The seed model's price readout is a deterministic function of storage
(`base_price·exp(price_k·(storage_normal−storage)/storage_normal)`), so given the
calibrated params + observed storage we get a **model-implied fair value** per date;
the **mispricing** is `market − model` (relative). When the market trades BELOW
model-implied fair value the gas is cheap vs fundamentals → long; above → short.
Emitting that mispricing as valuation-snapshot rows makes it gradeable on the SAME
instrument as level/change/divergence/etc. — the concrete "are we using the sysdyn
work" test. Reuses the pure `seed_gas` readout so live == model. Pure, stdlib-only.
"""

from __future__ import annotations

import os
import sys
from typing import Mapping, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.sysdyn.seed_gas import DEFAULT_PARAMS, _price_from_storage  # noqa: E402
from crypto_signals_data import build_percentile_snapshots  # noqa: E402


def model_implied_price(storage: float, params: Mapping[str, float]) -> float:
    """Model-implied fair value for a storage level, via the seed model's readout."""
    return _price_from_storage(float(storage), params)


def gas_mispricing_series(dated_storage, dated_price, params: Mapping[str, float],
                          *, relative: bool = True) -> list:
    """``[(date, mispricing), ...]`` on the dates present in BOTH storage and price.
    ``mispricing`` = ``market − model`` (``relative=True`` → divided by model, guarding
    a non-positive model). A NEGATIVE mispricing = market below fair value = cheap."""
    price_by = {str(d): float(v) for d, v in (dated_price or []) if v is not None}
    out = []
    for d, s in (dated_storage or []):
        if s is None:
            continue
        d = str(d)
        if d not in price_by:
            continue
        model = model_implied_price(s, params)
        market = price_by[d]
        if relative:
            if model <= 0:
                continue
            out.append((d, (market - model) / model))
        else:
            out.append((d, market - model))
    return sorted(out, key=lambda x: x[0])


def emit_mispricing_snapshots(dated_storage, dated_price, params: Optional[Mapping[str, float]] = None,
                              *, symbol: str = "UNG", asset_class: str = "commodity",
                              lookback: int = 156, min_history: int = 30,
                              relative: bool = True) -> list:
    """Emit the gas mispricing as valuation-snapshot rows (gradeable through the same
    S2+S3 grader). ``higher_is_cheaper=False``: a HIGHER mispricing (market rich vs
    model) reads richer, so ``cheap_score`` is high when the market is cheap vs fair
    value. Params default to the seed model's `DEFAULT_PARAMS` (the workflow passes the
    P1c-calibrated params)."""
    series = gas_mispricing_series(dated_storage, dated_price, params or DEFAULT_PARAMS,
                                   relative=relative)
    return build_percentile_snapshots(
        symbol, "sysdyn_gas_mispricing", series, asset_class=asset_class,
        lookback=lookback, min_history=min_history, higher_is_cheaper=False,
        note="M29 sysdyn model-implied mispricing (market vs storage-readout fair value)",
        source="sysdyn_mispricing",
    )


def load_calibrated_params(scorecard_path: Optional[str]) -> dict:
    """Read the fitted gas-seed params from an M29 dual/price calibration scorecard —
    ``{**fixed_params, **train_holdout.params}`` (the held-out fit is the PIT one). Falls
    back to `DEFAULT_PARAMS` when the path is missing/garbled or the fields are absent, so
    a run pre-dating the calibration still grades on the seed constants (never fatal)."""
    import json

    params = dict(DEFAULT_PARAMS)
    if not scorecard_path or not os.path.exists(scorecard_path):
        return params
    try:
        with open(scorecard_path) as f:
            card = json.load(f)
    except Exception:  # noqa: BLE001 — a garbled scorecard falls back to seed constants
        return params
    fixed = card.get("fixed_params") or {}
    fitted = (card.get("train_holdout") or {}).get("params") or {}
    for k, v in {**fixed, **fitted}.items():
        try:
            params[k] = float(v)
        except (TypeError, ValueError):
            continue
    return params


# ---------------------------------------------------------------------------
# CLI — fetch storage+price, emit the mispricing construction, grade it (S2+S3)
# ---------------------------------------------------------------------------


def _render(symbol: str, params: dict, graded: dict) -> str:
    s2 = graded.get("s2_signal", {}).get("summary", {})
    s3 = graded.get("s3_pnl", {}).get("summary", {})
    cw = graded.get("s3_pnl", {}).get("conviction_weighted", {}).get("full", {})
    return "\n".join([
        "M28 Phase B / M29 bridge — sysdyn gas MISPRICING construction (S2 signal + S3 PnL)",
        "=" * 74, "",
        f"symbol        : {symbol}",
        f"price_k       : {params.get('price_k')}   base_price: {params.get('base_price')}   "
        f"storage_normal: {params.get('storage_normal')}",
        f"verdict       : {graded.get('verdict', '—')}   worth_building: {graded.get('worth_building')}",
        f"S2 honest     : {bool(s2.get('any_honest_monetizable_horizon'))}",
        f"S3 pays_oos   : {bool(s3.get('pays_oos'))}   conv_ret: {cw.get('total_return')}   "
        f"sharpe: {cw.get('sharpe')}",
        f"meta          : {graded.get('meta', {})}",
    ])


def main(argv: Optional[list] = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(
        description="M29 gas mispricing → valuation-snapshot construction, graded S2+S3")
    ap.add_argument("--candles-dir", required=True, help="dir of proxy candle CSVs (needs UNG)")
    ap.add_argument("--symbol", default="UNG")
    ap.add_argument("--params-scorecard", default="comms/macro/sysdyn_gas_dual_scorecard.json",
                    help="M29 calibration scorecard to load fitted params from (falls back to seed)")
    ap.add_argument("--lookback", type=int, default=156)
    ap.add_argument("--min-history", type=int, default=30)
    ap.add_argument("--rebalance-every", type=int, default=7, help="gas storage is weekly")
    ap.add_argument("--horizons", default="7,14,30,60,90")
    ap.add_argument("--pnl-horizon", type=int, default=30)
    ap.add_argument("--fee-frac", type=float, default=0.0)
    ap.add_argument("--carry-frac-per-day", type=float, default=0.0)
    ap.add_argument("--absolute", action="store_true", help="use raw market−model (default: relative)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    from sysdyn_gas_data import fetch_eia_storage_dated, fetch_weekly_ng_price_dated
    from grade_construction import grade, load_close_panels, make_price_at

    storage = fetch_eia_storage_dated()
    price = fetch_weekly_ng_price_dated()
    params = load_calibrated_params(args.params_scorecard)

    records = emit_mispricing_snapshots(
        storage, price, params, symbol=args.symbol, lookback=args.lookback,
        min_history=args.min_history, relative=not args.absolute)

    if not records:
        graded = {"verdict": "insufficient_data", "worth_building": False,
                  "meta": {"n_storage": len(storage or []), "n_price": len(price or []),
                           "n_snapshots": 0}}
    else:
        price_at = make_price_at(load_close_panels(args.candles_dir))
        cfg = {"min_conviction": 0.4, "universe": [], "express_as": "debit_vertical",
               "account": "alpaca_options_paper"}
        graded = grade(records, price_at, cfg=cfg, rebalance_every=args.rebalance_every,
                       horizons=[int(x) for x in args.horizons.split(",")],
                       pnl_horizon=args.pnl_horizon, fee_frac=args.fee_frac,
                       carry_frac_per_day=args.carry_frac_per_day)
        graded.setdefault("meta", {}).update(
            {"n_snapshots": len(records), "n_storage": len(storage or []),
             "n_price": len(price or []), "params_source": args.params_scorecard,
             "relative": not args.absolute})

    print(_render(args.symbol, params, graded))
    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w") as f:
            json.dump(graded, f, indent=2, default=str)
            f.write("\n")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
