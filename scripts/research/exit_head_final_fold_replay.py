#!/usr/bin/env python3
"""Replay a leg's FINAL per-year fold with the head arm CHARGED ITS OWN COSTS.

WHY THIS EXISTS (RQ-20260929-401, manager review 2026-09-29, BLOCKING finding)
-----------------------------------------------------------------------------
`scripts/ml/train_exit_head.py` compares two arms that are NOT on the same cost
basis:

  baseline   fold["actual"]      = each trade's `final_r`, which the E0 builder takes
             from the harness `net_r`  (build_exit_head_dataset.py: "final_r":
             _f(r.get("net_r")), source `harness_net_r`) — AFTER fee + slippage
             + funding.
  head       fold["model_cond"]  = `open_r` at the exit bar (train_exit_head.
             replay_trade), and `open_r` is the raw candle mark
             (close - entry)/risk (build_exit_head_dataset.py) — GROSS.

So every trade the head truncates escapes its whole round-trip cost, plus the
extra cost of a discretionary market exit. That is a bias TOWARD a PASS, and an
earlier draft of this unit's text ("both arms carry the harness's cost
identically") was wrong. This script prices the head arm the RQ-20260928-005 way
(analyze_exit_head._policy_sim is the precedent): a truncated trade pays

    net = open_r(exit bar) - roundtrip_cost_r(entry, exit price, risk, entry ->
          exit-bar close time)  - exit_fee_r

  * roundtrip_cost_r is src.runtime.execution_costs — the ONE model the harness
    itself uses (fee DEFAULT_FEE_BPS_ROUNDTRIP + venue slippage + funding for the
    windows the SHORTER hold crossed), so a truncated trade pays less funding
    than the full hold, exactly as it would.
  * exit_fee_r = one side of slippage (slippage_bps_roundtrip / 2), charged only
    on a discretionary exit — the same convention as the research-exit-head-build
    workflow (`exit_fee_bps = slip / 2`).
  * a trade the head never truncates keeps its harness `net_r`, so the two arms
    are identical there.

TWO SELF-CHECKS, so a wrong number cannot pass quietly
  1. The replay must reproduce the trainer's OWN fold: the sum of gross head R and
     of baseline R here must equal `e1_report.json`'s `model_cond[...]` / `actual`
     `net_r` (which `agg` rounds to 2 dp) to within 0.011. Otherwise the leg reads
     `replay_mismatch`, not graded.
  2. The cost policy must be the harness's: for EVERY fold trade the emitted
     `cost_total_r` is recomputed from the same model with the resolved policy and
     must agree within 1e-3, else `cost_policy_mismatch`. The number of trades
     actually checked (`n_cost_checked`) must equal `n_oos`; a trade whose emitted
     cost cannot be recomputed is uncounted, not a zero gap, and any shortfall reads
     `cost_policy_unverified`. This is also the first
     actual check that the emitted `net_r` is net of the full venue cost stack.

`recovered_r_oos` is UNROUNDED (the trainer's `agg` rounds to 0.01R).

Usage (on the trainer, `.venv` active, after the round)::

    python scripts/research/exit_head_final_fold_replay.py \\
        --round-dir /home/ubuntu/rq20260929_401_round --tf 15m --expect-year 2026 \\
        --legs ict_scalp_sol_15m,ict_scalp_xrp_15m,ict_scalp_eth_15m
"""
# wiring: manual-only - the cost-charged replay for the one-off trainer round
# dispatched by research/queue/RQ-20260929-401.yaml (`run.note`); not called from CI.
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO, REPO / "scripts" / "ml"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.runtime import execution_costs as ec  # noqa: E402

TAU = 0.10                      # the shipped artifact's tau; ONE arm, fixed a priori
ARM = "below_half_r_tau_0.1"    # its key in the trainer's `model_cond`
TRAINER_ROUNDING = 0.011        # `agg` rounds net_r to 2 dp
COST_TOL = 1e-3                 # emitted cost_total_r vs recomputation, in R
MIN_FOLD_TRADES = 50            # the trainer's own --min-fold-trades default


def cost_policy(symbol: str) -> Dict[str, float]:
    """The harness CLI's own resolution (backtest_ict_scalp.main), via the one resolver."""
    slip, fund = ec.resolve_cost_policy(symbol)
    return {"fee_bps_roundtrip": float(ec.DEFAULT_FEE_BPS_ROUNDTRIP),
            "slippage_bps_roundtrip": float(slip),
            "funding_bps_per_window": float(fund),
            "exit_fee_bps": float(slip) / 2.0}


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _cost(policy: Dict[str, float], *, entry: float, exit_price: float, risk: float,
          entry_time: Any, exit_time: Any) -> Dict[str, float]:
    return ec.roundtrip_cost_r(
        entry=entry, exit_price=exit_price, risk=risk,
        entry_time=entry_time, exit_time=exit_time,
        fee_bps_roundtrip=policy["fee_bps_roundtrip"],
        slippage_bps_roundtrip=policy["slippage_bps_roundtrip"],
        funding_bps_per_window=policy["funding_bps_per_window"])


def head_exit_net_r(*, open_r: float, entry: float, risk: float, is_long: bool,
                    entry_time: Any, exit_time: Any,
                    policy: Dict[str, float]) -> Dict[str, float]:
    """Net R of a trade the head CLOSED EARLY at a bar whose mark is `open_r`."""
    exit_price = entry + (1.0 if is_long else -1.0) * open_r * risk
    cb = _cost(policy, entry=entry, exit_price=exit_price, risk=risk,
               entry_time=entry_time, exit_time=exit_time)
    per_r = (abs(entry) + abs(exit_price)) / 2.0 / risk
    exit_fee_r = policy["exit_fee_bps"] / 1.0e4 * per_r
    return {"net_r": open_r - cb["total_cost_r"] - exit_fee_r,
            "cost_r": cb["total_cost_r"], "exit_fee_r": exit_fee_r,
            "funding_windows": cb["funding_windows"]}


def baseline_cost_gap(row: Dict[str, Any], policy: Dict[str, float]) -> Optional[float]:
    """|emitted cost_total_r - recomputed| for one harness trade, or None if not derivable."""
    try:
        entry, risk, gross = float(row["entry"]), float(row["risk"]), float(row["gross_r"])
        emitted = float(row["cost_total_r"])
    except (KeyError, TypeError, ValueError):
        return None
    is_long = str(row.get("direction", "long")).lower() in ("long", "buy")
    exit_price = entry + (1.0 if is_long else -1.0) * gross * risk
    cb = _cost(policy, entry=entry, exit_price=exit_price, risk=risk,
               entry_time=row.get("entry_time"), exit_time=row.get("exit_time"))
    return abs(emitted - cb["total_cost_r"])


def replay_fold(trades: Dict[str, List[dict]], probs: Dict[str, Any],
                emit_by_key: Dict[str, dict], policy: Dict[str, float], tf_s: int,
                decide) -> Dict[str, Any]:
    """Pure replay of one fold. `decide(bars, probs)` -> index of the exit bar or None
    (train_exit_head.policy_model_cond semantics: None / last bar = held to the end)."""
    base = head_gross = head_net = costs = fees = 0.0
    n = n_early = 0
    worst_gap = 0.0
    n_cost_checked = 0
    missing: List[str] = []
    for tk, bars in trades.items():
        e = emit_by_key.get(tk)
        if e is None:
            missing.append(tk)
            continue
        n += 1
        b = float(bars[0]["final_r"])
        base += b
        idx = decide(bars, probs[tk])
        if idx is None or idx >= len(bars) - 1:
            head_gross += b
            head_net += b
        else:
            n_early += 1
            bar = bars[idx]
            r = head_exit_net_r(
                open_r=float(bar["open_r"]), entry=float(e["entry"]), risk=float(e["risk"]),
                is_long=str(e.get("direction", "long")).lower() in ("long", "buy"),
                entry_time=e.get("entry_time"), exit_time=_iso(int(bar["bar_t"]) + tf_s),
                policy=policy)
            head_gross += float(bar["open_r"])
            head_net += r["net_r"]
            costs += r["cost_r"]
            fees += r["exit_fee_r"]
        gap = baseline_cost_gap(e, policy)
        if gap is not None:                # an underivable trade is NOT a zero gap: it is uncounted
            n_cost_checked += 1
            worst_gap = max(worst_gap, gap)
    return {"n_oos": n, "n_early_exits": n_early, "join_missing": missing,
            "baseline_net_r": base, "head_gross_r": head_gross, "head_net_r": head_net,
            "recovered_r_oos": head_net - base, "recovered_r_gross": head_gross - base,
            "charged_roundtrip_cost_r": costs, "charged_exit_fee_r": fees,
            "worst_baseline_cost_gap_r": worst_gap, "n_cost_checked": n_cost_checked}


def replay_leg(round_dir: Path, leg: str, tf: str, expect_year: int) -> Dict[str, Any]:
    """Train/score the leg's final-year fold exactly as train_exit_head does, then replay it."""
    rep_p = round_dir / leg / "e1_report.json"
    emit_p = round_dir / "emit" / f"{leg}.jsonl"
    rows_p = round_dir / leg / "rows.jsonl"
    for p in (rep_p, emit_p, rows_p):
        if not p.exists():
            return {"leg": leg, "state": "no_report", "missing": str(p)}
    import train_exit_head as T                      # scripts/ml, unmodified
    from build_exit_head_dataset import _epoch      # noqa: PLC0415 - the builder's own key maker
    report = json.loads(rep_p.read_text())
    if report.get("fold_mode") != "years":
        return {"leg": leg, "state": "wrong_fold_mode", "fold_mode": report.get("fold_mode")}
    tf_s = T.TF_S[tf]
    h_trades = T.group_trades([r for r in T.load_rows(rows_p) if r["source"] == "harness"])
    blocks = T.fold_blocks(h_trades, "years", MIN_FOLD_TRADES, lambda bars: bars[0]["bar_t"])
    match = [b for b in blocks if b[1] == int(expect_year)]
    if not match or len(match[0][2]) < MIN_FOLD_TRADES:
        return {"leg": leg, "state": "final_fold_missing", "expect_year": expect_year,
                "n_test": len(match[0][2]) if match else 0}
    _label, _year, test, y0 = match[0]
    train_rows = [r for _tk, b in h_trades.items() for r in b
                  if b[-1]["bar_t"] < y0 - T.EMBARGO_S]
    if len(train_rows) < T._MIN_FOLD_TRAIN_ROWS:
        return {"leg": leg, "state": "final_fold_missing", "why": "thin_train",
                "train_rows": len(train_rows)}
    model = T.train_model(train_rows)
    all_rows = [r for bars in test.values() for r in bars]
    X, _y = T.matrix(all_rows)
    p = model.predict_proba(X)[:, 1]
    probs, i = {}, 0
    for tk, bars in test.items():
        probs[tk] = p[i:i + len(bars)]
        i += len(bars)
    cond = T._SHAPES["below_half_r"]

    def decide(bars, pr):
        res = T.policy_model_cond(bars, pr, TAU, cond)
        return res["bars"] - 1 if res["bars"] < len(bars) else None

    emit_by_key: Dict[str, dict] = {}
    for line in emit_p.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        emit_by_key[f"{r.get('strategy') or 'unknown'}:{r.get('symbol') or 'unknown'}:"
                    f"{int(_epoch(r.get('entry_time')))}"] = r
    symbol = next(iter(emit_by_key.values()))["symbol"]
    out = replay_fold(test, probs, emit_by_key, cost_policy(symbol), tf_s, decide)
    out.update({"leg": leg, "fold_year": int(expect_year), "symbol": symbol,
                "cost_policy": cost_policy(symbol), "train_rows": len(train_rows)})
    fold = next((f for f in report.get("folds", []) if int(f.get("year", -1)) == int(expect_year)), None)
    if out["join_missing"]:
        out["state"] = "join_incomplete"
    elif fold is None:
        out["state"] = "replay_mismatch"
        out["why"] = "trainer report has no fold for the year"
    else:
        tr_base = fold["actual"]["net_r"]
        tr_head = (fold.get("model_cond") or {}).get(ARM, {}).get("net_r")
        out["trainer_baseline_net_r"], out["trainer_head_gross_r"] = tr_base, tr_head
        if (fold.get("n_trades") != out["n_oos"] or tr_head is None
                or abs(tr_base - out["baseline_net_r"]) > TRAINER_ROUNDING
                or abs(tr_head - out["head_gross_r"]) > TRAINER_ROUNDING):
            out["state"] = "replay_mismatch"
        elif out["n_cost_checked"] != out["n_oos"]:
            # the cost self-check ran on fewer trades than are graded: a trade whose emitted
            # cost could not be recomputed must not pass as a zero gap
            out["state"] = "cost_policy_unverified"
        elif out["worst_baseline_cost_gap_r"] > COST_TOL:
            out["state"] = "cost_policy_mismatch"
        else:
            out["state"] = "ok"
    (round_dir / leg / "final_fold_net.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    return out


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--round-dir", required=True)
    ap.add_argument("--legs", required=True)
    ap.add_argument("--tf", required=True)
    ap.add_argument("--expect-year", type=int, required=True)
    a = ap.parse_args(argv[1:])
    res = []
    for leg in a.legs.split(","):
        try:
            res.append(replay_leg(Path(a.round_dir), leg, a.tf, a.expect_year))
        except Exception as exc:  # noqa: BLE001  # allow-silent: NOT silent - the stack trace is logged below and the leg is recorded as state `replay_failed`, which exit_head_final_fold_grade maps to not_applicable / producer_failed (a crashed leg is never dropped from the denominator)
            traceback.print_exc(file=sys.stderr)
            res.append({"leg": leg, "state": "replay_failed",
                        "error": f"{type(exc).__name__}: {exc}"[:300]})
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
