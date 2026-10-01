import json
import numpy as np
import pandas as pd
from scripts.research import e38_pooled_trend_donchian as P


def _frame(n=400, planted=False, seed=0):
    rng = np.random.default_rng(seed)
    t = pd.date_range("2025-01-01", periods=n, freq="8h", tz="UTC")
    sym = np.where(np.arange(n) % 2 == 0, "ETHUSDT", "SOLUSDT")
    rv = rng.random(n)
    er = rng.random(n)
    net = rng.normal(0, 1, n) + (2.0 * (er - 0.5) if planted else 0)
    return pd.DataFrame({"leg": "x", "symbol": sym, "entry_time": t, "direction": "long",
                         "net_r": net, "rv20": rv, "er20": er})


def test_dedupe_keeps_first_and_distinct_timeframes():
    t = pd.Timestamp("2025-01-01 10:05", tz="UTC")
    a = pd.DataFrame({"symbol": ["ETHUSDT"] * 2, "entry_time": [t, t + pd.Timedelta(hours=5)],
                      "direction": ["long", "long"], "net_r": [1.0, 2.0]})
    b = pd.DataFrame({"symbol": ["ETHUSDT"], "entry_time": [t + pd.Timedelta(minutes=20)],
                      "direction": ["long"], "net_r": [9.0]})  # same hour+dir => duplicate of a[0]
    d = P.dedupe({"a": a, "b": b})
    assert len(d) == 2 and 9.0 not in d["net_r"].tolist()


def test_planted_signal_detected_and_null_not():
    hit = P.analyse(_frame(planted=True))
    assert hit["label"] in ("regime_dependence_detected", "in_sample_only") and hit["feature"] == "er20"
    null = P.analyse(_frame(planted=False, seed=3))
    assert null["label"] == "no_regime_signal"
    assert P.grade(null) in ("fail", "indeterminate") and P.grade({"label": "regime_dependence_detected"}) == "pass"


def test_end_to_end_no_data_is_not_a_verdict(tmp_path):
    rc = P.main(["--out", str(tmp_path), "--ledgers", str(tmp_path / "none/*__trades.jsonl")])
    v = json.loads((tmp_path / "verdict.json").read_text())
    assert rc == 0 and v["read_state"] == "no_data" and v["verdict"] == "indeterminate"
