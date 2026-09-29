"""The head arm must pay the costs the baseline arm already pays (manager review, BLOCKING)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
_spec = importlib.util.spec_from_file_location(
    "exit_head_final_fold_replay", REPO / "scripts/research/exit_head_final_fold_replay.py")
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)
from src.runtime import execution_costs as ec  # noqa: E402

POL = R.cost_policy("SOLUSDT")
ENTRY, RISK = 100.0, 2.0
T0 = "2026-03-01T00:00:00+00:00"


def _emit(gross_r=1.0, direction="long", exit_time="2026-03-01T06:00:00+00:00"):
    is_long = direction == "long"
    xp = ENTRY + (1 if is_long else -1) * gross_r * RISK
    cb = ec.roundtrip_cost_r(
        entry=ENTRY, exit_price=xp, risk=RISK, entry_time=T0, exit_time=exit_time,
        fee_bps_roundtrip=POL["fee_bps_roundtrip"], slippage_bps_roundtrip=POL["slippage_bps_roundtrip"],
        funding_bps_per_window=POL["funding_bps_per_window"])
    return {"strategy": "s", "symbol": "SOLUSDT", "direction": direction, "entry": ENTRY, "risk": RISK,
            "entry_time": T0, "exit_time": exit_time, "gross_r": gross_r,
            "net_r": gross_r - cb["total_cost_r"], "cost_total_r": cb["total_cost_r"]}


def _bars(open_rs, final_r, t_open=1772323200):
    return [{"final_r": final_r, "open_r": o, "bar_t": t_open + 900 * i} for i, o in enumerate(open_rs)]


def test_policy_is_the_venue_default_and_positive():
    assert POL["slippage_bps_roundtrip"] > 0 and POL["exit_fee_bps"] == POL["slippage_bps_roundtrip"] / 2
    assert POL["fee_bps_roundtrip"] == ec.DEFAULT_FEE_BPS_ROUNDTRIP


def test_a_truncated_trade_pays_roundtrip_cost_and_exit_fee():
    r = R.head_exit_net_r(open_r=-0.4, entry=ENTRY, risk=RISK, is_long=True, entry_time=T0,
                          exit_time="2026-03-01T01:00:00+00:00", policy=POL)
    assert r["cost_r"] > 0 and r["exit_fee_r"] > 0
    assert abs(r["net_r"] - (-0.4 - r["cost_r"] - r["exit_fee_r"])) < 1e-12
    assert r["net_r"] < -0.4                     # strictly worse than the gross mark the trainer scored


def test_short_side_prices_the_exit_on_the_correct_side():
    lo = R.head_exit_net_r(open_r=0.3, entry=ENTRY, risk=RISK, is_long=False, entry_time=T0,
                           exit_time="2026-03-01T01:00:00+00:00", policy=POL)
    assert lo["net_r"] < 0.3 and lo["cost_r"] > 0


def test_replay_fold_charges_only_truncated_trades():
    trades = {"held": _bars([0.1, 0.2, 0.3, 0.4], 0.8),          # never triggers -> keeps harness net
              "cut": _bars([0.0, -0.3, -0.6, -0.9], -1.0),       # triggers at bar 1 -> truncated
              "no_open_r": _bars([0.9, 1.0, 1.1, 1.2], 1.5)}     # low score but open_r >= 0.5 -> not cut
    probs = {"held": [.9] * 4, "cut": [.5, .05, .05, .05], "no_open_r": [.05] * 4}
    emit = {k: _emit(gross_r=1.0) for k in trades}

    def decide(bars, p):                                          # below_half_r @ tau .10, first bar
        for i, (b, pr) in enumerate(zip(bars, p)):
            if pr < 0.10 and b["open_r"] < 0.5:
                return i
        return None

    out = R.replay_fold(trades, probs, emit, POL, 900, decide)
    assert out["n_oos"] == 3 and out["n_early_exits"] == 1 and out["join_missing"] == []
    assert out["baseline_net_r"] == 0.8 - 1.0 + 1.5
    assert abs(out["head_gross_r"] - (0.8 - 0.3 + 1.5)) < 1e-12   # the trainer's own (gross) head figure
    assert out["head_net_r"] < out["head_gross_r"]                # cost + exit fee make it worse
    assert abs((out["head_gross_r"] - out["head_net_r"])
               - (out["charged_roundtrip_cost_r"] + out["charged_exit_fee_r"])) < 1e-12
    assert out["recovered_r_oos"] == out["head_net_r"] - out["baseline_net_r"]     # unrounded
    assert out["worst_baseline_cost_gap_r"] < R.COST_TOL         # emitted cost matches the policy


def test_join_gaps_and_a_different_cost_policy_are_visible():
    trades = {"a": _bars([0.0, 0.1], 0.5), "b": _bars([0.0, 0.1], 0.5)}
    out = R.replay_fold(trades, {"a": [.9, .9], "b": [.9, .9]}, {"a": _emit()}, POL, 900, lambda b, p: None)
    assert out["join_missing"] == ["b"] and out["n_oos"] == 1
    other = dict(POL, slippage_bps_roundtrip=POL["slippage_bps_roundtrip"] + 20.0)
    assert R.baseline_cost_gap(_emit(), other) > R.COST_TOL         # a wrong policy cannot pass the check
    assert R.baseline_cost_gap({"entry": 1}, POL) is None           # underivable != zero gap


def test_replay_leg_end_to_end_reproduces_the_trainers_own_fold(tmp_path, monkeypatch):
    """Drive replay_leg through the REAL train_exit_head fold/eval code with a stub booster.

    Builds a fake round dir (rows.jsonl, emit/<leg>.jsonl, e1_report.json produced by the
    trainer's own eval_split) and checks: the 2026 fold is picked, trades JOIN to the emit by
    the builder's key, the trainer cross-check passes, and the delta is head_net - baseline.
    """
    import json

    import numpy as np
    sys.path.insert(0, str(REPO / "scripts" / "ml"))
    import build_exit_head_dataset as B
    import train_exit_head as T

    class Stub:
        def predict_proba(self, X):
            open_r = X[:, T.FEATURES.index("open_r")]
            p1 = np.where(open_r < 0.0, 0.05, 0.9)       # low score on losers -> the head fires
            return np.column_stack([1 - p1, p1])

    monkeypatch.setattr(T, "train_model", lambda rows: Stub())
    monkeypatch.setattr(T, "auc_score", lambda y, p: 0.5)    # sklearn is trainer-only; AUC is not under test
    leg, sym = "ict_scalp_sol_15m", "SOLUSDT"
    rnd = tmp_path
    (rnd / leg).mkdir()
    (rnd / "emit").mkdir()
    rows, emits = [], []
    t0 = 1735689600      # 2025-01-01
    for yr_off, n_tr in ((0, 60), (365 * 86400, 55)):        # a 2025 train year, a 2026 test year
        for k in range(n_tr):
            t_open = t0 + yr_off + k * 6 * 3600
            et = B.datetime.fromtimestamp(t_open, tz=B.timezone.utc)
            is_long = k % 2 == 0
            gross = -1.0 if k % 3 == 0 else 1.5
            e = {"strategy": leg, "symbol": sym, "direction": "long" if is_long else "short",
                 "entry_time": str(et), "exit_time": str(et.replace(hour=et.hour)), "entry": 100.0,
                 "risk": 2.0, "gross_r": gross}
            e["exit_time"] = str(B.datetime.fromtimestamp(t_open + 12 * 900, tz=B.timezone.utc))
            xp = 100.0 + (1 if is_long else -1) * gross * 2.0
            cb = ec.roundtrip_cost_r(entry=100.0, exit_price=xp, risk=2.0, entry_time=e["entry_time"],
                                     exit_time=e["exit_time"], fee_bps_roundtrip=POL["fee_bps_roundtrip"],
                                     slippage_bps_roundtrip=POL["slippage_bps_roundtrip"],
                                     funding_bps_per_window=POL["funding_bps_per_window"])
            e["cost_total_r"], e["net_r"] = cb["total_cost_r"], gross - cb["total_cost_r"]
            emits.append(e)
            key = f"{leg}:{sym}:{int(B._epoch(e['entry_time']))}"
            for a in range(12):
                m = -0.2 * a if gross < 0 else 0.15 * a
                rows.append({"source": "harness", "trade_key": key, "strategy": leg, "symbol": sym,
                             "bar_t": t_open + a * 900, "year": et.year, "age_bars": a, "open_r": m,
                             "final_r": e["net_r"], "direction": e["direction"], "holding_pays": int(gross > 0),
                             **{f: 0.1 for f in T.FEATURES if f not in ("open_r", "age_bars", "is_long")}})
    (rnd / leg / "rows.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (rnd / "emit" / f"{leg}.jsonl").write_text("\n".join(json.dumps(e) for e in emits))
    h = T.group_trades([T.load_rows(rnd / leg / "rows.jsonl")[i] for i in range(len(rows))])
    blocks = T.fold_blocks(h, "years", 50, lambda b: b[0]["bar_t"])
    y0 = [b for b in blocks if b[1] == 2026][0]
    res = T.eval_split(Stub(), y0[2], T.TF_S["15m"])
    res.update({"year": 2026, "n_trades": len(y0[2])})
    (rnd / leg / "e1_report.json").write_text(json.dumps({"fold_mode": "years", "folds": [res]}))

    out = R.replay_leg(rnd, leg, "15m", 2026)
    assert out["state"] == "ok", out
    assert out["n_oos"] == 55 and out["n_early_exits"] > 0 and out["join_missing"] == []
    assert out["recovered_r_oos"] == out["head_net_r"] - out["baseline_net_r"]
    assert out["head_net_r"] < out["head_gross_r"]                     # the head paid for its exits
    assert (rnd / leg / "final_fold_net.json").exists()
    assert R.replay_leg(rnd, leg, "15m", 2027)["state"] == "final_fold_missing"   # no borrowed year
    # a trainer report that disagrees with the replay must NOT be graded
    res["actual"]["net_r"] += 0.5
    (rnd / leg / "e1_report.json").write_text(json.dumps({"fold_mode": "years", "folds": [res]}))
    assert R.replay_leg(rnd, leg, "15m", 2026)["state"] == "replay_mismatch"
