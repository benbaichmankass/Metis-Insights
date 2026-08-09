#!/usr/bin/env python3
"""Replay the LIVE exit head over backtest trades — the trainer-side augment leg.

WHY THIS EXISTS
---------------
`trend_donchian` declares three live exit-head levers
(``exit_head_model``/``exit_head_threshold``/``exit_head_action``). The trend
harness cannot express them, so
``regime_debt_matrix`` lists them in ``_UNREPLAYABLE`` and the leg records
``fidelity: approximate``. Since PR #8605 the calibrator CONSUMES that claim, so
an approximate leg can never be stamped ``calibrated`` — which made the
earned-trust path unreachable for any strategy that graduates an ML exit head.

**The blocker is the ARTIFACT LOCATION, not the model registry.** The exit head
is a self-contained JSON (``booster_txt`` inline, loaded by
``src/runtime/exit_head_shadow.py::_load_artifacts``) published into
``runtime_logs/trainer_mirror/exit_head/``. It is not committed, so
``research-backtest-augment.yml`` on ``ubuntu-latest`` has no copy of it — the
trainer and the live VM do. Design-doc §5e recorded this as "needs the model
registry at inference"; that premise is wrong in a way that matters, because it
made the fix sound like a registry port rather than "run this leg where the
artifact already lives". Operator decision (2026-08-08): move the leg to the
trainer.

WHAT IT DOES
------------
1. Runs the **live-faithful** harness ``scripts/backtest_trend.py`` (see §5f of
   the design doc for why that copy and not ``scripts/research/`` — it is the
   one whose trail freezes the entry ATR, matching ``monitor()``), capturing
   full ``Trade`` objects via ``trades_out``.
2. For each trade, walks its in-trade CLOSED bars ``entry_index+1 ..
   exit_index`` and, at each, builds the live feature row with
   ``exit_head_shadow._feature_row`` (the same function the monitor calls, so
   live == replay) and scores it with the published head.
3. The FIRST bar at which ``exit_head_shadow.would_exit_for`` fires re-resolves
   the trade: it exits at that bar's close. The decision predicate is imported,
   never re-implemented — a second copy of it is precisely the defect class
   §5f documents.
4. Emits per-trade before/after R plus an aggregate, and (``--emit-trades``) a
   JSONL the augment recorder can ingest as a **faithful** leg.

HONEST FAILURE
--------------
No artifact / no LightGBM / no in-trade bars is **exit 2 with a named reason**,
never a silent pass-through: emitting the unchanged trades under an
"exit-head-applied" label would be the manufactured-number class
(``src/runtime/provenance.py``) one level up. Only ``exit 0`` means the head
actually scored.

Observe-only, Tier-1: reads candles + a published artifact, writes only its own
outputs. Nothing here touches the live order path.

Usage (on the trainer, where the artifact lives)::

    python scripts/ml/exit_head_replay.py --data /tmp/btc_1h.csv \\
        --symbol BTCUSDT --timeframe 1h --strategy trend_donchian \\
        --emit-trades /tmp/trend_donchian__exithead.jsonl --json /tmp/out.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

# THREE dirnames, not two: this file is scripts/ml/exit_head_replay.py, so
# dirname^2 lands on `scripts/`, not the repo root. Every consumer below joins a
# REPO-ROOT-relative path onto it, so the off-by-one broke all three at once —
# `scripts/scripts/backtest_trend.py` (_load_harness), `scripts/config/strategies.yaml`
# (main, the failure observed in issue #8646), and `from src.utils.paths import`
# in default_artifact_dir() once sys.path carried `scripts/` instead of the root.
# Pinned by test_exit_head_replay_repo_root_resolves_to_the_actual_repo_root.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

HARNESS_REL = "scripts/backtest_trend.py"   # the live-faithful copy (§5f)


class ReplayUnavailable(RuntimeError):
    """The head could not be scored. Carries the reason; never a silent pass."""


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "_bt_trend_live_faithful", os.path.join(_REPO_ROOT, HARNESS_REL))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_bt_trend_live_faithful"] = mod
    spec.loader.exec_module(mod)
    return mod


def _apply_venue_cost_policy(harness, symbol: str) -> Dict[str, Any]:
    """Put the harness on the venue cost policy, so the emitted ``net_r`` is what
    it says it is.

    ``scripts/backtest_trend.py`` keeps its cost terms in module globals that
    default to ``slippage=0.0 / funding=0.0``, and only ``main()`` (the CLI path)
    resolves the venue-aware values into them. That default is **deliberate and
    load-bearing** — PR #8468 chose it so in-process callers (the confidence
    sweep, the ML recorder, the M30 panel bridge) stay byte-identical — and is
    not changed here.

    But this module imports the harness and calls ``run_backtest`` directly, so
    it inherited the fee-only basis while writing a field named ``net_r`` under a
    comment promising *fee + slippage + funding*. Every CLI harness fills that
    same JSONL schema with net-of-FULL-cost, and the recorder that ingests it
    labels ``won = net_r > 0`` — so a leg on a cheaper basis flips the label on
    any trade whose true net is marginally negative. The module docstring calls
    this emit a **faithful** leg; a leg costed differently from its siblings is
    the one thing it must not be.

    Trades are unaffected: the engine's entry/exit loop reads no cost term (costs
    are applied post-hoc in ``_summarize``/``_cost_breakdown``), so this changes
    ``net_r`` and nothing about which trades exist or where they exit. The
    replay's own headline delta is computed from gross ``r_multiple`` and is
    likewise unchanged.

    Returns the effective terms so the caller can state the basis rather than
    leave the reader to assume it.
    """
    from src.runtime import execution_costs
    slip, fund = execution_costs.resolve_cost_policy(symbol)
    harness.SLIPPAGE_BPS_ROUNDTRIP = slip
    harness.FUNDING_BPS_PER_WINDOW = fund
    return {"fee_bps_roundtrip": harness.FEE_BPS_ROUNDTRIP,
            "slippage_bps_roundtrip": slip,
            "funding_bps_per_window": fund,
            "funding_window_hours": harness.FUNDING_WINDOW_HOURS}


def default_artifact_dir() -> str:
    from src.utils.paths import runtime_logs_dir
    return str(runtime_logs_dir() / "trainer_mirror" / "exit_head")


def load_heads(artifact_dir: str, timeframe: str,
               symbol: str) -> List[Tuple[Dict[str, Any], Any]]:
    """Every published head servable for ``(timeframe, symbol)``.

    Mirrors the live in-distribution guard in ``maybe_score_exit_head``: a head
    only scores its own trained timeframe, and its declared symbol list when it
    carries one. Raises rather than returning ``[]`` so "no head" can never be
    mistaken for "the head said hold".
    """
    if not os.path.isdir(artifact_dir):
        raise ReplayUnavailable(
            f"no exit-head artifact dir at {artifact_dir} — this leg must run "
            f"where the trainer mirror is published (the trainer VM), not on a "
            f"GitHub-hosted runner")
    paths = sorted(p for p in os.listdir(artifact_dir) if p.endswith(".json"))
    if not paths:
        raise ReplayUnavailable(f"artifact dir {artifact_dir} holds 0 *.json heads")
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise ReplayUnavailable(f"lightgbm unavailable: {exc}") from exc
    out: List[Tuple[Dict[str, Any], Any]] = []
    skipped: List[str] = []
    for name in paths:
        with open(os.path.join(artifact_dir, name), encoding="utf-8") as fh:
            artifact = json.load(fh)
        if str(artifact.get("tf") or "") != timeframe:
            skipped.append(f"{name}(tf={artifact.get('tf')})")
            continue
        symbols = artifact.get("symbols")
        if symbols and symbol not in symbols:
            skipped.append(f"{name}(symbols)")
            continue
        out.append((artifact, lgb.Booster(model_str=artifact["booster_txt"])))
    if not out:
        raise ReplayUnavailable(
            f"{len(paths)} head(s) present, 0 servable for "
            f"(tf={timeframe}, symbol={symbol}); skipped: {', '.join(skipped)}")
    return out


def replay_trade(candles, trade, artifact: Dict[str, Any],
                 predict: Callable[[List[List[float]]], float],
                 action: str) -> Dict[str, Any]:
    """Re-resolve ONE trade's exit under the head. Returns a per-trade record.

    ``predict`` takes the feature matrix and returns the head's raw score, so a
    test can drive the decision path without LightGBM. The take/hold predicate
    itself is ``exit_head_shadow.would_exit_for`` — imported, never copied.
    """
    from src.runtime.exit_head_shadow import _feature_row, would_exit_for

    features = artifact.get("features") or []
    entry_idx = trade.entry_index + 1        # STRICTLY after the signal bar
    rec: Dict[str, Any] = {
        "entry_time": str(trade.entry_time), "direction": trade.direction,
        "baseline_r": trade.r_multiple, "baseline_outcome": trade.outcome,
        "exit_head_fired": False, "bars_scored": 0,
        "replayed_r": trade.r_multiple, "replayed_outcome": trade.outcome,
    }
    if action != "close":
        rec["note"] = f"exit_head_action={action!r} is not an apply action"
        return rec
    is_long = trade.direction == "long"
    for i in range(entry_idx, trade.exit_index + 1):
        row = _feature_row(candles.iloc[:i + 1], trade.entry, trade.risk,
                           trade.direction, entry_idx)
        if row is None:
            continue
        rec["bars_scored"] += 1
        vec = [[float(row[f]) if row.get(f) is not None else float("nan")
                for f in features]]
        # provenance: predict — the head's RAW score. Its meaning is
        # shape-dependent (below_half_r fires LOW, peak_* fire HIGH), which is
        # why the take/hold call is delegated to would_exit_for rather than
        # compared against a threshold here.
        score = float(predict(vec))
        if not would_exit_for(artifact.get("shape") or {}, score, row["open_r"]):
            continue
        close = float(candles["close"].iloc[i])
        r = ((close - trade.entry) if is_long else (trade.entry - close)) / trade.risk
        rec.update({
            "exit_head_fired": True, "exit_bar_index": i,
            "exit_time": str(candles["timestamp"].iloc[i]),
            "score": round(score, 6), "open_r_at_exit": round(row["open_r"], 4),
            "replayed_r": round(r, 4), "replayed_outcome": "exit_head",
        })
        return rec
    return rec


def _load_candles(path: str, resample: Optional[str]):
    import pandas as pd
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    if "timestamp" not in cols and "ts" in cols:
        df = df.rename(columns={cols["ts"]: "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if resample:
        rule = resample.strip().lower()
        if rule.endswith("m") and not rule.endswith("min"):
            rule = rule[:-1] + "min"
        agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
        if "volume" in df.columns:
            agg["volume"] = "sum"
        df = (df.set_index("timestamp").resample(rule, label="right", closed="right")
              .agg(agg).dropna().reset_index())
    return df


def split_in_sample(artifact: Dict[str, Any], *, bar_times, entry_times,
                    baseline_rs, replayed_rs) -> Dict[str, Any]:
    """Split a replay into its IN-SAMPLE and FORWARD halves, off the artifact.

    BL-20260808-EXIT-HEAD-MANIFEST-RECORDS-NO-TRAINING-WINDOW. A replay delta is
    worthless as evidence without knowing how much of the scored window the head
    was FITTED on. The first measured replay (#8653) reported delta_gross_r
    +10.804 over 2026-02-09 -> 2026-08-07 against a head whose data ended
    ~2026-07-12 — roughly 5 of 6 months in-sample — and that had to be
    reconstructed by hand from ``trained_at``.

    So the split ships as FIELDS, not as a caveat a reader has to remember. Same
    shape ``/performance`` uses for ``rCoverage``: report the honest
    sub-population beside the headline, and make "we don't know" an EXPLICIT
    state rather than a silent fallback to the headline.

    ``train_end`` is the DATA bound. ``trained_at`` is the wall-clock moment of
    fitting and is deliberately NOT substituted for it — conflating the two is
    the defect. A pre-fix artifact carries no ``train_end``, so every derived
    field is ``None`` and ``train_window_present`` is ``False``.

    A trade is FORWARD when its entry is strictly after ``train_end``; a bar is
    forward on the same rule. Boundary rows count as in-sample, which is the
    conservative direction (it can only make the forward sub-population smaller
    and therefore the evidence claim weaker, never inflated).

    NOTE the trap this must not invite: do NOT widen the replay window to "get
    more data" — a longer window makes the in-sample fraction LARGER. The fix for
    evidence quality is a forward-only window (start > train_end).
    """
    import pandas as pd

    raw = artifact.get("train_end")
    train_end = pd.to_datetime(raw, utc=True, errors="coerce") if raw else None
    if train_end is not None and pd.isna(train_end):
        train_end = None

    out: Dict[str, Any] = {
        "train_window_present": train_end is not None,
        "train_start": artifact.get("train_start"),
        "train_end": raw,
        "train_window_coverage": artifact.get("train_window_coverage"),
        "train_trades": artifact.get("train_trades"),
        "in_sample_bars": None, "forward_bars": None,
        "in_sample_trades": None, "forward_trades": None,
        "forward_baseline_gross_r": None, "forward_replayed_gross_r": None,
        "forward_delta_gross_r": None,
    }
    if train_end is None:
        return out

    bars = pd.to_datetime(pd.Series(list(bar_times)), utc=True, errors="coerce")
    out["in_sample_bars"] = int((bars <= train_end).sum())
    out["forward_bars"] = int((bars > train_end).sum())

    fwd_base = fwd_new = 0.0
    fwd_n = 0
    for et, b_r, n_r in zip(entry_times, baseline_rs, replayed_rs):
        ts = pd.to_datetime(et, utc=True, errors="coerce")
        if pd.isna(ts) or ts <= train_end:
            continue
        fwd_n += 1
        fwd_base += b_r
        fwd_new += n_r
    out["forward_trades"] = fwd_n
    out["in_sample_trades"] = len(list(entry_times)) - fwd_n
    out["forward_baseline_gross_r"] = round(fwd_base, 4)
    out["forward_replayed_gross_r"] = round(fwd_new, 4)
    out["forward_delta_gross_r"] = round(fwd_new - fwd_base, 4)
    return out


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="Replay the live exit head over trend-harness backtest trades.")
    p.add_argument("--data", required=True)
    p.add_argument("--resample", default=None)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--strategy", default="trend_donchian",
                   help="config/strategies.yaml key the params + exit_head_* come from")
    p.add_argument("--artifact-dir", default=None)
    p.add_argument("--emit-trades", default=None)
    p.add_argument("--json", dest="json_out", default=None)
    a = p.parse_args(argv[1:])

    import yaml
    with open(os.path.join(_REPO_ROOT, "config/strategies.yaml"), encoding="utf-8") as fh:
        conf = yaml.safe_load(fh)
    strategies = conf.get("strategies", conf)
    cfg = strategies.get(a.strategy)
    if not isinstance(cfg, dict):
        print(f"ERROR: no strategy {a.strategy!r} in config/strategies.yaml",
              file=sys.stderr)
        return 2
    action = str(cfg.get("exit_head_action") or "")
    if not cfg.get("exit_head_model"):
        print(f"ERROR: {a.strategy} declares no exit_head_model — nothing to "
              f"replay (this leg is only for exit-head-carrying strategies)",
              file=sys.stderr)
        return 2

    harness = _load_harness()
    cost_basis = _apply_venue_cost_policy(harness, a.symbol)
    df = _load_candles(a.data, a.resample)
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
        heads = load_heads(a.artifact_dir or default_artifact_dir(),
                           a.timeframe, a.symbol)
    except ReplayUnavailable as exc:
        print(f"COULD NOT MEASURE: {exc}", file=sys.stderr)
        return 2
    artifact, booster = heads[0]

    def predict(vec):
        return booster.predict(vec)[0]

    records = [replay_trade(df, t, artifact, predict, action) for t in trades]
    fired = [r for r in records if r["exit_head_fired"]]
    base_r = sum(r["baseline_r"] for r in records)
    new_r = sum(r["replayed_r"] for r in records)

    # --- IN-SAMPLE SPLIT (BL-20260808-EXIT-HEAD-MANIFEST-RECORDS-NO-TRAINING-WINDOW)
    # The delta below is worthless as evidence without knowing how much of the
    # scored window the head was FITTED on. The first measured replay (#8653)
    # reported delta_gross_r +10.804 over 2026-02-09 -> 2026-08-07 against a head
    # whose data ended ~2026-07-12 — roughly 5 of 6 months in-sample — and that
    # had to be reconstructed by hand from `trained_at`, which is the wall-clock
    # moment of fitting, not the data bound.
    #
    # So the split ships as FIELDS, not as a caveat a reader has to remember.
    # Same shape /performance uses for rCoverage: report the honest sub-population
    # beside the headline, and make "we don't know" an explicit state rather than
    # a silent fallback to the headline. A pre-fix artifact carries no
    # `train_end`, so every field here is None and `train_window_present` is
    # False — never inferred from `trained_at`.
    #
    # NOTE the direction of the trap: do NOT widen the replay window to "get more
    # data". A longer window makes the in-sample fraction LARGER. The fix for
    # evidence quality is a forward-only window (start > train_end).
    in_sample = split_in_sample(
        artifact,
        bar_times=list(df["timestamp"]),
        entry_times=[t.entry_time for t in trades],
        baseline_rs=[r["baseline_r"] for r in records],
        replayed_rs=[r["replayed_r"] for r in records])

    payload = {
        "strategy": a.strategy, "symbol": a.symbol, "timeframe": a.timeframe,
        "model_id": artifact.get("model_id"), "stage": artifact.get("stage"),
        "shape": artifact.get("shape"), "exit_head_action": action,
        "heads_servable": len(heads),
        "population": {
            "bars": int(len(df)),
            "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
            "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
            "trades": len(records),
            "trades_scored": sum(1 for r in records if r["bars_scored"] > 0),
            "trades_exit_head_fired": len(fired),
        },
        "in_sample_split": in_sample,
        "baseline_gross_r": round(base_r, 4),
        "replayed_gross_r": round(new_r, 4),
        "delta_gross_r": round(new_r - base_r, 4),
        # Net-of-FULL-cost since 2026-08-09 (see _apply_venue_cost_policy); it
        # was fee-only before, because the harness globals default to 0 and only
        # the CLI resolved them. `cost_basis` is emitted beside it so the number
        # is never read without its basis.
        "baseline_summary_net_total_r": baseline.get("net_total_r"),
        "cost_basis": cost_basis,
        "trades_detail": records,
    }
    print(f"exit-head replay — {a.strategy} {a.symbol} {a.timeframe} "
          f"(head {artifact.get('model_id')}, stage {artifact.get('stage')})")
    print(f"  population: {len(df)} bars, {len(records)} trades, "
          f"{payload['population']['trades_scored']} scored, "
          f"{len(fired)} re-resolved by the head")
    print(f"  cost basis: fee={cost_basis['fee_bps_roundtrip']}bps "
          f"slip={cost_basis['slippage_bps_roundtrip']}bps "
          f"funding={cost_basis['funding_bps_per_window']}bps/"
          f"{cost_basis['funding_window_hours']}h (venue policy)")
    print(f"  gross R: baseline {base_r:+.3f} -> replayed {new_r:+.3f} "
          f"(delta {new_r - base_r:+.3f})")
    # State the in-sample split next to the delta, never only in the JSON — the
    # delta ALONE is the number that gets quoted, so the qualifier has to travel
    # with it. `train_end` is the DATA bound off the artifact; `trained_at` (the
    # fitting moment) is deliberately not used as a substitute.
    if in_sample["train_window_present"]:
        print(f"  training window: {in_sample['train_start']} -> "
              f"{in_sample['train_end']} "
              f"(coverage {in_sample['train_window_coverage']}, "
              f"{in_sample['train_trades']} train trades)")
        print(f"  in-sample / forward: bars {in_sample['in_sample_bars']}/"
              f"{in_sample['forward_bars']}, trades "
              f"{in_sample['in_sample_trades']}/{in_sample['forward_trades']}")
        if in_sample["forward_trades"]:
            print(f"  FORWARD-ONLY gross R: baseline "
                  f"{in_sample['forward_baseline_gross_r']:+.3f} -> replayed "
                  f"{in_sample['forward_replayed_gross_r']:+.3f} "
                  f"(delta {in_sample['forward_delta_gross_r']:+.3f}) "
                  f"on n={in_sample['forward_trades']} — THIS is the "
                  f"out-of-sample figure; the headline delta above is not")
        else:
            print("  FORWARD-ONLY gross R: n/a — 0 trades entered after "
                  "train_end, so the headline delta is ENTIRELY in-sample")
    else:
        print("  in-sample split: UNKNOWN — this artifact records no `train_end` "
              "(pre-BL-20260808 export). The headline delta cannot be qualified; "
              "re-export the head with scripts/ml/export_exit_head.py to get it. "
              "`trained_at` is the fitting moment, NOT the data bound, and is "
              "deliberately not substituted here.")

    if a.emit_trades:
        with open(a.emit_trades, "w", encoding="utf-8") as fh:
            for t, r in zip(trades, records):
                fh.write(json.dumps({
                    "strategy": a.strategy, "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": r["replayed_r"],
                    # _fee_r is the harness's TOTAL round-trip cost in R
                    # (fee+slippage+funding); the legacy name is kept there.
                    # The venue policy is applied in main() via
                    # _apply_venue_cost_policy, so this is net-of-FULL-cost and
                    # comparable with every CLI harness's emit — it was fee-only
                    # until 2026-08-09 because the harness globals default to 0.
                    "net_r": round(r["replayed_r"] - harness._fee_r(t), 4),
                    # State the basis in the row: a consumer that mixes legs can
                    # check it instead of assuming they share a cost model.
                    "cost_basis": cost_basis,
                    "confidence": t.confidence,
                    "exit_head_applied": True,
                    "exit_head_fired": r["exit_head_fired"],
                }, default=str) + "\n")
        print(f"emit -> {a.emit_trades}", file=sys.stderr)
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2, default=str))
        print(f"JSON -> {a.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
