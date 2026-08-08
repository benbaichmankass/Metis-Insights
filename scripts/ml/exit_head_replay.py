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
        "baseline_gross_r": round(base_r, 4),
        "replayed_gross_r": round(new_r, 4),
        "delta_gross_r": round(new_r - base_r, 4),
        "baseline_summary_net_total_r": baseline.get("net_total_r"),
        "trades_detail": records,
    }
    print(f"exit-head replay — {a.strategy} {a.symbol} {a.timeframe} "
          f"(head {artifact.get('model_id')}, stage {artifact.get('stage')})")
    print(f"  population: {len(df)} bars, {len(records)} trades, "
          f"{payload['population']['trades_scored']} scored, "
          f"{len(fired)} re-resolved by the head")
    print(f"  gross R: baseline {base_r:+.3f} -> replayed {new_r:+.3f} "
          f"(delta {new_r - base_r:+.3f})")

    if a.emit_trades:
        with open(a.emit_trades, "w", encoding="utf-8") as fh:
            for t, r in zip(trades, records):
                fh.write(json.dumps({
                    "strategy": a.strategy, "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": r["replayed_r"],
                    # _fee_r is the harness's TOTAL round-trip cost in R
                    # (fee+slippage+funding); the legacy name is kept there.
                    "net_r": round(r["replayed_r"] - harness._fee_r(t), 4),
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
