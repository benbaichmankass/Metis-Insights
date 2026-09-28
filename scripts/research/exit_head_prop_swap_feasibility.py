#!/usr/bin/env python3
"""Exit-head-for-prop-legs feasibility look (RQ-20260927-003 / wave-6 lane M2,
prop look) -- trend_donchian_sol_prop / trend_donchian_eth_prop.

WHY THIS EXISTS
---------------
``config/strategies.yaml`` declares ``exit_head_model: exit-head-donchian-1h-v1``
on ``trend_donchian``, ``trend_donchian_sol`` and ``trend_donchian_eth`` (the
non-prop siblings) but NOT on ``trend_donchian_sol_prop`` /
``trend_donchian_eth_prop`` -- the two legs actually live on the prop account
``breakout_1`` (a daily-swap venue, real rate 0.033%/day per Breakout's help
docs, config/strategies.yaml:2687). An exit head that shortens a losing hold
saves swap accrual on a daily-swap venue in a way it does not on a perpetual
(8h-funding) venue, so the question is worth asking on its own -- not merely
inherited from the non-prop legs' shadow track record.

WHAT THIS DOES, AND WHAT IT IS NOT
------------------------------------
This is a FEASIBILITY LOOK (exit-refinement P1/P2 evidence-gathering stage),
NOT a full prop EV/survival validation gate -- that is
``scripts/prop/validate_alt_prop.py`` (a different harness,
``scripts.backtest_system`` + Monte Carlo, built for the promotion decision
itself). This script instead reuses the SAME live-faithful
``scripts/backtest_trend.py`` harness ``exit_head_replay.py`` uses -- the two
prop variants share ``trend_donchian.monitor()`` with their non-prop siblings,
just with different YAML exit geometry (``trail_mult``/``tp_r``), so the same
harness is methodologically the right one to ask "would the ALREADY-PUBLISHED
head's decisions transfer onto this leg's own trade population".

It does NOT retrain -- it loads the already-published, already-shadow-scored
``exit-head-donchian-1h-v1`` artifact via ``exit_head_replay.load_heads()``
(unmodified), which is legitimate here specifically because that artifact's
own training set already includes SOLUSDT/ETHUSDT rows (multi-symbol training,
confirmed in RQ-20260927-001's own measurement). It DOES bypass
``exit_head_replay.main()``'s ``cfg.get("exit_head_model")`` gate, because that
gate exists to stop an UNDECLARED leg from being treated as if it had a live
exit-head wire -- the exact opposite of what a feasibility look needs to ask
"what WOULD this leg look like if it had one". No config is read for
``exit_head_model``/``exit_head_action`` and none is written; this changes no
live behaviour.

SWAP ADJUSTMENT
----------------
``execution_costs.resolve_cost_policy(symbol)`` (used everywhere else in this
family) resolves Bybit-style 8h PERPETUAL funding, not Breakout's flat daily
CFD-style swap (``scripts/prop/validate_alt_prop.py --cost-model daily_swap``
is the existing tool that already draws this distinction). Both arms
(baseline held-to-exit vs replayed early-exit) are charged bars_held x an
implied per-bar swap-in-R rate, so the DELTA an early exit saves is:

    swap_saved_r = (bars_held_baseline - bars_held_replayed) * swap_r_per_bar

which is exactly the quantity "recovered R" should include on a daily-swap
venue that ``recovered_r_oos`` (gross replayed - gross baseline, no swap term)
does not.  ``swap_r_per_bar`` converts the daily rate to the strategy's native
bar (1h) and to R units via each trade's own ``risk`` (entry-to-stop distance),
matching the same "R-normalises risk" convention the rest of this family uses
-- and per the exit-refinement skill's "definition of done" #7, this is
STATED as a per-trade R-normalisation, not silently assumed cost-free.

Usage (on the trainer, where the published artifact + candle data live)::

    python3 scripts/research/exit_head_prop_swap_feasibility.py \\
        --strategy trend_donchian_sol_prop --symbol SOLUSDT --timeframe 1h \\
        --swap-rate-daily 0.00033 --json /tmp/prop_sol.json
"""
# wiring: manual-only - a one-off research driver dispatched by hand via a
# trainer-vm-diag issue/workflow_dispatch (research/queue/RQ-20260927-003.yaml
# `run.workflow`), the same pattern RQ-20260927-001's driver used. It is not
# meant to be called from a registered CI/scheduled workflow -- see that
# unit's `run.note` for why a one-off dispatch is the right shape here rather
# than a permanent workflow input.
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, List

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "ml"))

_TF_HOURS = {"5m": 5 / 60, "15m": 0.25, "30m": 0.5, "1h": 1.0, "2h": 2.0,
             "4h": 4.0, "1d": 24.0}


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--strategy", required=True,
                   help="e.g. trend_donchian_sol_prop / trend_donchian_eth_prop "
                        "-- ANY config/strategies.yaml key, exit_head_model "
                        "declared or not (this script does not gate on it).")
    p.add_argument("--symbol", required=True)
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--swap-rate-daily", type=float, default=0.00033,
                   help="Breakout's real flat daily swap rate (confirmed via "
                        "their help docs, config/strategies.yaml:2687). "
                        "Default 0.00033 = 0.033%%/day.")
    p.add_argument("--artifact-dir", default=None)
    p.add_argument("--json", dest="json_out", default=None)
    a = p.parse_args(argv[1:])

    import yaml
    from exit_head_replay import (  # scripts/ml/exit_head_replay.py
        _load_harness, _apply_venue_cost_policy, replay_trade,
        load_heads, default_artifact_dir, ReplayUnavailable,
    )
    from scripts.ops import backtest_data_source

    with open(os.path.join(_REPO_ROOT, "config/strategies.yaml"), encoding="utf-8") as fh:
        conf = yaml.safe_load(fh)
    strategies = conf.get("strategies", conf)
    cfg = strategies.get(a.strategy)
    if not isinstance(cfg, dict):
        print(f"ERROR: no strategy {a.strategy!r} in config/strategies.yaml", file=sys.stderr)
        return 2

    res = backtest_data_source.resolve_or_refuse(a.symbol, a.timeframe, None)
    if not res.ok:
        print(backtest_data_source.refusal_message(
            res, harness="exit_head_prop_swap_feasibility.py",
            legacy_default=None), file=sys.stderr)
        return 2

    harness = _load_harness()
    cost_basis = _apply_venue_cost_policy(harness, a.symbol)
    df = harness._load_candles(res.path)
    if res.resample:
        # BUG FIXED HERE (was silently absent): backtest_data_source flags
        # `resample` whenever the resolver had to fall back to a finer native
        # grain than the requested timeframe (SOLUSDT/ETHUSDT have no native
        # 1h file, only 5m/15m, so a 1h request resolves to the 5m CSV with
        # resample="1h"). Every OTHER caller of this resolver applies the flag
        # (exit_head_replay.py::_load_candles(path, resample) is the sibling
        # pattern this mirrors) -- this driver read the flag into `res` and
        # then never consulted it, so the two prior dispatches of this unit
        # silently ran the leg's 1h-geometry params (donchian/atr/trail_mult/
        # timeout_bars=200) against RAW 5-MINUTE bars: ~12x the intended bar
        # count (a real cost-of-runtime driver, this is why both prior
        # dispatches hit the trainer-vm-diag 60-minute job cap) and a
        # different instrument entirely from what config/strategies.yaml
        # declares for this leg.
        df = harness._resample(df, res.resample)

    trades: List[Any] = []
    baseline = harness.run_backtest(
        df.copy(), donchian=int(cfg.get("donchian", 20)),
        atr_period=int(cfg.get("atr_period", 14)),
        atr_stop_mult=float(cfg.get("atr_stop_mult", 2.5)),
        trail_mult=float(cfg.get("trail_mult", 3.0)),
        timeout_bars=200, cooldown_bars=1, timeframe=a.timeframe,
        symbol=a.symbol, min_confidence=float(cfg.get("min_confidence", 0.0)),
        long_only=bool(cfg.get("long_only")), trades_out=trades)

    try:
        heads = load_heads(a.artifact_dir or default_artifact_dir(), a.timeframe, a.symbol)
    except ReplayUnavailable as exc:
        print(f"COULD NOT MEASURE: {exc}", file=sys.stderr)
        return 2
    artifact, booster = heads[0]

    # This BINARY LightGBM booster (train_exit_head.py trains on the binary
    # target `holding_pays` via lgb.LGBMClassifier) is exported as a raw
    # lgb.Booster; on a binary objective, Booster.predict() returns the
    # positive-class probability, never a class label or a multiclass max —
    # there is no multiclass head in this family. Interpreted by
    # would_exit_for per the artifact's declared shape, same as
    # exit_head_replay.py's own identical closure.
    def predict(vec):
        # provenance: predict — the head's RAW score, P(class=1) for this binary booster (see above)
        return booster.predict(vec)[0]

    # Bypassed by design (see module docstring): this leg declares no
    # exit_head_action in YAML, so the counterfactual always asks "if this
    # leg were wired to CLOSE, unconditionally on the head's fire".
    action = "close"
    records = [replay_trade(df, t, artifact, predict, action) for t in trades]

    tf_hours = _TF_HOURS.get(a.timeframe, 1.0)
    swap_r_per_bar_by_trade = []
    detail = []
    for t, r in zip(trades, records):
        bars_baseline = int(t.exit_index - t.entry_index)
        bars_replayed = (int(r["exit_bar_index"] - t.entry_index)
                        if r.get("exit_head_fired") else bars_baseline)
        # swap accrues on notional held, expressed here in R units via this
        # trade's OWN risk (entry-to-stop distance) -- R-normalised, per the
        # exit-refinement "definition of done" #7 convention.
        swap_r_per_bar = (a.swap_rate_daily * (tf_hours / 24.0)) * (t.entry / t.risk)
        swap_saved_r = (bars_baseline - bars_replayed) * swap_r_per_bar
        swap_r_per_bar_by_trade.append(swap_r_per_bar)
        detail.append({
            "entry_time": str(t.entry_time), "bars_held_baseline": bars_baseline,
            "bars_held_replayed": bars_replayed,
            "baseline_r": r["baseline_r"], "replayed_r": r["replayed_r"],
            "exit_head_fired": r["exit_head_fired"], "swap_saved_r": round(swap_saved_r, 5),
        })

    base_r = sum(r["baseline_r"] for r in records)
    new_r = sum(r["replayed_r"] for r in records)
    swap_saved_total_r = sum(d["swap_saved_r"] for d in detail)
    n_fired = sum(1 for r in records if r["exit_head_fired"])

    payload = {
        "schema_version": 1,
        "research_unit": "RQ-20260927-003",
        "strategy": a.strategy, "symbol": a.symbol, "timeframe": a.timeframe,
        "model_id": artifact.get("model_id"), "stage": artifact.get("stage"),
        "note": (
            "COUNTERFACTUAL / feasibility look -- this leg declares no "
            "exit_head_model in config/strategies.yaml; nothing here reads or "
            "writes that config. Scored with the ALREADY-PUBLISHED, "
            "already-trained artifact (no retrain), so this figure is NOT "
            "out-of-sample the way RQ-20260927-001/-002 are -- it inherits "
            "whatever in-sample bias those units exist to correct for. Treat "
            "as a scoping signal for whether the follow-up (retrain + "
            "walk-forward on this leg's own geometry) is worth doing, not as "
            "promotion evidence."),
        "population": {
            "bars": int(len(df)), "trades": len(records),
            "trades_scored": sum(1 for r in records if r["bars_scored"] > 0),
            "trades_exit_head_fired": n_fired,
            "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
            "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        },
        "cost_basis_perp_funding_proxy": cost_basis,
        "swap_rate_daily": a.swap_rate_daily,
        "swap_rate_note": (
            "Breakout's real flat daily swap (0.033%/day, config/"
            "strategies.yaml:2687), applied as bars_held_delta x per-bar-R "
            "swap rate -- NOT the perp-funding cost_basis above, which this "
            "leg's real venue does not charge."),
        "baseline_gross_r": round(base_r, 4),
        "baseline_summary_net_total_r": baseline.get("net_total_r"),
        "replayed_gross_r": round(new_r, 4),
        "delta_gross_r": round(new_r - base_r, 4),
        "swap_saved_total_r": round(swap_saved_total_r, 4),
        "recovered_r_incl_swap": round((new_r - base_r) + swap_saved_total_r, 4),
        "trades_detail": detail,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    out = json.dumps(payload, indent=2, sort_keys=True)
    print(out)
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            fh.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
