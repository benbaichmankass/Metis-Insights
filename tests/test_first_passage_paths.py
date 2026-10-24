"""FIRST-PASSAGE-ON-PATHS: first_passage.py --paths / --harness-legs (scripts/research/first_passage_paths.py).

Positive and negative controls via the module's own self-test (a planted ADX-regime signal PASSES the info rule and
the policy rule; a driftless walk never PASSES either; a broken emit fails parity and forces INDETERMINATE; the
gambler's-ruin closed form holds on a fixed-bracket walk), the no-lookahead state rule, the as-traded label, the
--ledgers mode byte-identity, and an end-to-end harness run on synthetic candles that writes a script_run-shaped
verdict.json.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts" / "research"), str(REPO / "scripts"), str(REPO)]
import first_passage as fp  # noqa: E402
import first_passage_paths as fpp  # noqa: E402

# sha256 of verdict.json from first_passage.run() (--ledgers mode) on its own synthetic corpus, computed on the
# code BEFORE the --paths build (2026-10-24). The --paths options are added to the same parser; this pins that
# the ledgers path writes byte-identical records.
LEDGERS_GOLDEN = {
    "census_False": "0a285427f404d5219aa38d4499eda41f63c6043b7482290eae15b8f4ee560a7c",
    "baseline_False": "32472758acb0b7762bbca6a13b1725febc296eb265b9246a9eee001ab224cf45",
    "policy_False": "3d0cb85be45931d28ac5324087ee0bc95ef4b21d3688642eae898027e83311c8",
    "census_True": "6129214f12c4ccaee2ef5d90f8dae29e6c5682f7d816ebe10b871cda4c9ae70f",
    "baseline_True": "65aa5564c0979795698e0a3137e48564d12edaae7400269a6595b0f0f0f3684f",
    "policy_True": "c9472ad6bd71f0928b87d73134d0c6e8b77925beb1af3b0ad0972d18503affc0",
}


def test_ledgers_mode_byte_identical():
    got = {}
    with tempfile.TemporaryDirectory() as td:
        for drift in (False, True):
            df, rows = fp._synthetic(drift, n_trades=200)
            legs = [{"leg": "synth_1h", "symbol": "SYNUSDT", "tf": "1h", "rows": rows, "ledger": "-",
                     "n": len(rows), "frame": df}]
            for mode in ("census", "baseline", "policy"):
                o = Path(td) / f"{mode}{drift}"
                fp.run(fp._args(["--mode", mode, "--out", str(o), "--policy-tp-r", "2.0", "--min-n", "50",
                                 "--features", "rich"]), legs_override=legs)
                got[f"{mode}_{drift}"] = hashlib.sha256((o / "verdict.json").read_bytes()).hexdigest()
    assert got == LEDGERS_GOLDEN


def test_ledgers_args_default_off():
    a = fp._args(["--out", "x"])
    assert a.paths is None and a.harness_legs is None


def test_self_test_controls():
    assert fpp._self_test() == 0


def _trade(n_bars=8, exit_r=-1.0):
    rows = []
    for b in range(1, n_bars + 1):
        c = 0.1 * b
        rows.append({"trade_key": "leg_x|S|2021-01-01 00:00:00+00:00|long", "bar_offset": b, "c_r": c,
                     "fav_r": c + 0.05, "adv_r": c - 0.05, "open_r": c, "peak_r": c + 0.05, "bars_since_peak": 0,
                     "stop_r": -1.0, "target_r": 2.0, "dist_to_stop_r": c + 1.0, "dist_to_target_r": 2.0 - c,
                     "atr_r": 0.3, "adx_14": 20.0 + b, "is_exit": False})
    rows[-1].update(is_exit=True, exit_reason="stop", open_r=exit_r)
    trow = {"strategy": "leg_x", "symbol": "S", "entry_time": "2021-01-01 00:00:00+00:00", "direction": "long",
            "exit_time": "2021-01-02 08:00:00+00:00", "entry": 100.0, "sl": 98.0, "exit_reason": "stop",
            "gross_r": exit_r, "net_r": exit_r - 0.1, "mfe_r": 0.85, "cost_fee_r": 0.05, "cost_slippage_r": 0.02,
            "cost_funding_r": 0.03}
    return rows, trow


def test_states_never_read_past_bar_t_and_label_is_as_traded():
    rows, trow = _trade()
    t, rep = fpp.build_trades("leg_x", rows, [trow], "4h", cost_fn=lambda *_: 0.02)
    assert rep["state"] == "included" and rep["n_exit_r_mismatch_gross_r"] == 0
    e = fpp.events(t[0], ["leg_x"])
    assert e["n"] == len(rows) - 1  # the exit row is never a decision state
    # L_t = 1 iff exit_r - open_r_t < -c_exit
    assert np.array_equal(e["y"], ((-1.0 - t[0]["open_r"][:-1]) < -0.02).astype(float))
    # mutate everything after bar 4: the first 4 states' features must not move
    rows2 = [dict(r) for r in rows]
    for r in rows2[4:-1]:
        r.update(c_r=9.0, fav_r=9.5, adv_r=8.5, open_r=9.0, adx_14=99.0, dist_to_stop_r=10.0)
    t2, _ = fpp.build_trades("leg_x", rows2, [trow], "4h", cost_fn=lambda *_: 0.02)
    e2 = fpp.events(t2[0], ["leg_x"])
    np.testing.assert_array_equal(np.nan_to_num(e["X1"][:4], nan=-7.0), np.nan_to_num(e2["X1"][:4], nan=-7.0))


def test_parity_failure_is_reported_not_pooled():
    rows, trow = _trade()
    trow["mfe_r"] = 3.0
    _, rep = fpp.build_trades("leg_x", rows, [trow], "4h", cost_fn=lambda *_: 0.02)
    assert rep["state"] == "parity_failed" and rep["parity_share"] == 0.0


def test_c_exit_comes_from_the_one_cost_policy():
    from src.runtime.execution_costs import resolve_cost_policy
    slip, _ = resolve_cost_policy("ETHUSDT")
    assert abs(fpp.c_exit_r("ETHUSDT", 2000.0, 40.0) - slip / 1e4 * 2000.0 / 40.0) < 1e-12


def test_end_to_end_harness_paths_writes_a_measured_verdict(tmp_path):
    import pandas as pd
    rng = np.random.default_rng(7)
    ts = pd.date_range("2021-01-01", "2025-01-01", freq="4h", tz="UTC")
    c = 2000 * np.exp(np.cumsum(rng.normal(0.0002, 0.015, len(ts))))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, len(ts))))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, len(ts))))
    csv = tmp_path / "c.csv"
    pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S%z"), "open": o, "high": h, "low": lo,
                  "close": c, "volume": 1000.0}).to_csv(csv, index=False)
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(REPO / "scripts/research/first_passage.py"),
                        "--harness-legs", "trend_donchian_eth_4h", "--data-csv", str(csv), "--mode", "policy",
                        "--min-n", "20", "--min-folds", "5", "--n-boot", "200", "--out", str(out)],
                       cwd=REPO, capture_output=True, text=True, check=False, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    v = json.loads((out / "verdict.json").read_text())
    assert v["read_state"] == "measured" and v["verdict"] in ("pass", "fail", "indeterminate") and v["population"]
    leg = v["measurement"]["legs"][0]
    assert leg["state"] == "included" and leg["parity_share"] >= 0.99 and leg["n_exit_r_mismatch_gross_r"] == 0
    assert v["measurement"]["info"]["n_oos_trades"] > 0 and "per_family" in v["measurement"]["policy"]
