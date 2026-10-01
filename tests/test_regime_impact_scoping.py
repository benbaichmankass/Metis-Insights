"""regime_impact_scoping: selection is train-only, noise is applied at test time, the
oracle recovers a planted losing cell, and a no-signal leg gives no headroom."""
import numpy as np
import pandas as pd
import pytest

from scripts.research import regime_impact_scoping as R


def test_confusion_rows_sum_to_one_and_hit_accuracy():
    for model in ("ordinal", "uniform"):
        for a in (0.55, 0.6, 0.7):
            c = R.confusion(model, a)
            assert np.allclose(c.sum(axis=1), 1.0) and np.allclose(np.diag(c), a)
    assert R.confusion("ordinal", 0.6)[0, 2] == 0.0  # chop never mislabelled as trending


def _leg(n=120, planted=True, seed=0):
    rng = np.random.default_rng(seed)
    lab = rng.integers(0, 3, n)
    direction = np.where(rng.random(n) < 0.5, "long", "short")
    net = rng.normal(0.05, 0.6, n)
    if planted:  # trending+long loses in EVERY block
        net = np.where((lab == 2) & (direction == "long"), -0.8 + rng.normal(0, 0.2, n), net)
    return {"true_lab": lab, "direction": direction, "net_r": net}


def test_oracle_recovers_planted_cell_and_noise_degrades_it():
    legs = {f"l{i}": _leg(seed=i) for i in range(8)}
    out = R.scope(legs, seeds=200)["results"]["ordinal"]
    assert out["oracle"]["pooled_delta_r"]["mean"] > 5
    assert out["A=0.70"]["pooled_delta_r"]["mean"] < out["oracle"]["pooled_delta_r"]["mean"]
    assert out["A=0.55"]["pooled_delta_r"]["mean"] <= out["A=0.70"]["pooled_delta_r"]["mean"]
    assert R.grade({"ordinal": out}, 8)[0] in ("pass", "indeterminate")


def test_no_signal_gives_no_headroom():
    legs = {f"l{i}": _leg(planted=False, seed=i) for i in range(8)}
    res = R.scope(legs, seeds=200)["results"]
    assert R.grade(res, 8) == ("fail", "no_headroom_trend_axis")


def test_selection_uses_train_blocks_only():
    # A cell that loses ONLY in the test block must not be selected.
    n = 40
    lab = np.zeros(n, dtype=int)
    direction = np.array(["long"] * n)
    net = np.ones(n)            # winning everywhere in blocks 0..2
    net[30:] = -5.0             # last block loses
    sel = R.select_cells(R.cell_ids(lab, direction), net, np.arange(0, 30))
    assert len(sel) == 0


def test_too_few_legs_is_could_not_measure():
    assert R.grade({}, 2) == ("indeterminate", "could_not_measure")


def test_trend_labels_no_lookahead():
    n = 200
    t = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    c = pd.DataFrame({"open_time": t, "high": np.linspace(100, 300, n) + 1, "low": np.linspace(100, 300, n) - 1,
                      "close": np.linspace(100, 300, n)})
    # entry exactly at the close of bar 100 => bar 100 is closed, bar 101 is not
    e = pd.Series([t[100] + pd.Timedelta(hours=1)])
    lab = R.trend_labels(c, e, "1h")
    assert lab == ["trending"]
    early = pd.Series([t[5]])
    assert R.trend_labels(c, early, "1h") == [None]


def test_end_to_end_with_synthetic_candles(tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    n = 3000
    t = pd.date_range("2025-09-01", periods=n, freq="4h", tz="UTC")
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    candles = pd.DataFrame({"open_time": t, "high": close + 1, "low": close - 1, "close": close})
    d = tmp_path / "runs" / "2026-09-25"
    d.mkdir(parents=True)
    import json
    for i, leg in enumerate(["trend_donchian_eth_4h", "trend_donchian_sol_4h", "trend_donchian_ada_4h",
                             "trend_donchian_avax_4h", "trend_donchian_xrp_4h"]):
        rows = [{"strategy": "trend_donchian", "symbol": "ETHUSDT", "direction": "long" if j % 2 else "short",
                 "entry_time": str(t[200 + j * 20]), "net_r": float(rng.normal(0, 1))} for j in range(60)]
        (d / f"{leg}__trades.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(R, "fetch_candles", lambda *a, **k: candles)
    rc = R.main(["--out", str(tmp_path / "o"), "--seeds", "200", "--ledgers", str(tmp_path / "runs/*/*__trades.jsonl")])
    assert rc == 0
    v = json.loads((tmp_path / "o/verdict.json").read_text())
    assert v["read_state"] == "measured" and v["measurement"]["scoping_only_not_a_promotion_case"] is True
    assert v["measurement"]["n_legs"] == 5 and "vol NOT covered" in v["measurement"]["axis"]
    with pytest.raises(SystemExit):
        R.main(["--out", str(tmp_path / "o2"), "--seeds", "10"])


def test_policy_cell_legs_reads_real_policy():
    keys = R.policy_cell_legs()
    assert "trend_donchian" in keys and "gld_pullback_1h" in keys
    assert "trend_donchian_eth_4h" not in keys   # an uncovered live leg


def test_default_selection_is_the_701_rule_and_conservative_selects_fewer():
    assert R.SELECTION == {"min_cell_n": 8, "loss_se": 0.0}
    rng = np.random.default_rng(5)
    n = 200
    lab = rng.integers(0, 3, n)
    direction = np.where(rng.random(n) < 0.5, "long", "short")
    net = rng.normal(-0.02, 1.0, n)            # barely negative everywhere: noise, not a loss
    cell = R.cell_ids(lab, direction)
    train = np.arange(0, 150)
    loose = R.select_cells(cell, net, train)
    try:
        R.SELECTION.update(min_cell_n=20, loss_se=1.0)
        strict = R.select_cells(cell, net, train)
    finally:
        R.SELECTION.update(min_cell_n=8, loss_se=0.0)
    assert len(strict) <= len(loose) and set(strict) <= set(loose)


def test_conservative_selection_keeps_a_real_planted_loss():
    legs = {f"l{i}": _leg(seed=i) for i in range(8)}      # planted trending+long loss of -0.8 R
    try:
        R.SELECTION.update(min_cell_n=20, loss_se=1.0)
        out = R.scope(legs, seeds=200)["results"]["ordinal"]
    finally:
        R.SELECTION.update(min_cell_n=8, loss_se=0.0)
    assert out["oracle"]["pooled_delta_r"]["mean"] > 3
