"""scripts/research/train_parity.py -- the off-VM vs trainer training parity gate (row KAGGLE-TRAIN-RUNNER-FIRST)."""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("lightgbm")

from scripts.research import train_parity as tp  # noqa: E402

MANIFEST = "eth-regime-1h-lgbm-v1"
REL = Path("market_features/ETHUSDT/1h/v002")


def _shard(root: Path, n: int = 1500, seed: int = 7) -> Path:
    rng = random.Random(seed)
    d = root / "datasets-out" / REL
    d.mkdir(parents=True)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lines = []
    for i in range(n):
        ts = t0 + timedelta(hours=i)
        vol = abs(rng.gauss(0.01, 0.005))
        r = rng.gauss(0, vol)
        lines.append(json.dumps({
            "ts": ts.isoformat().replace("+00:00", "Z"), "symbol": "ETHUSDT", "timeframe": "1h",
            "vol_bucket": "high" if vol > 0.012 else "low", "rolling_log_return_vol": vol, "log_return": r,
            "log_return_lag_1": rng.gauss(0, 0.01), "log_return_lag_2": rng.gauss(0, 0.01),
            "hour_of_day": ts.hour, "dayofweek": ts.weekday(),
            "regime_label": "volatile" if vol + 0.3 * abs(r) > 0.013 else "range"}))
    (d / "data.jsonl").write_text("\n".join(lines) + "\n")
    (d / "metadata.json").write_text(json.dumps({"row_count": n}))
    return root


def _run(tmp: Path, backend: str, data: Path, monkeypatch, ref=None) -> dict:
    monkeypatch.setenv("ML_DATA_ROOT", str(data))
    if ref is not None:
        monkeypatch.setattr(tp, "latest_reference", lambda unit: ref)
    out = tmp / f"out-{backend}"
    argv = ["--manifest", MANIFEST, "--cutoff", "2026-02-15T00:00:00Z", "--backend", backend, "--out", str(out)]
    if ref is not None:
        argv += ["--reference-unit", "RQ-20261015-aaaa-01"]
    assert tp.main(argv) == 0
    return {"rec": json.loads((out / tp.RECORD).read_text()), "verdict": json.loads((out / "verdict.json").read_text())}


def test_same_rows_same_model_passes(tmp_path, monkeypatch):
    data = _shard(tmp_path / "a")
    ref = _run(tmp_path, "trainer", data, monkeypatch)
    assert ref["verdict"]["verdict"] == "pass" and ref["verdict"]["read_state"] == "measured"
    assert ref["rec"]["dataset"]["rows_used"] == 45 * 24      # Jan 1 .. Feb 14 inclusive, hourly
    cur = _run(tmp_path, "kaggle_cpu", data, monkeypatch, ref=ref["rec"])
    assert cur["verdict"]["verdict"] == "pass", cur["verdict"]
    assert cur["verdict"]["measurement"]["agreement"] == 1.0


def test_rows_after_cutoff_do_not_change_the_input(tmp_path, monkeypatch):
    short = _shard(tmp_path / "a", n=1200)
    long_ = _shard(tmp_path / "b", n=1500)            # same seed: identical prefix, extra rows past the cutoff
    ref = _run(tmp_path, "trainer", short, monkeypatch)
    cur = _run(tmp_path, "kaggle_cpu", long_, monkeypatch, ref=ref["rec"])
    assert cur["rec"]["dataset"]["sha256_used"] == ref["rec"]["dataset"]["sha256_used"]
    assert cur["verdict"]["verdict"] == "pass"


def test_different_rows_is_indeterminate_not_fail(tmp_path, monkeypatch):
    ref = _run(tmp_path, "trainer", _shard(tmp_path / "a", seed=1), monkeypatch)
    cur = _run(tmp_path, "kaggle_cpu", _shard(tmp_path / "b", seed=2), monkeypatch, ref=ref["rec"])
    assert cur["verdict"]["verdict"] == "indeterminate"
    assert "input differs" in cur["verdict"]["note"]


def test_metric_outside_tolerance_fails():
    base = {"dataset": {"sha256_used": "x", "rows_used": 10}, "class_labels": ["range", "volatile"],
            "metrics": {"accuracy": 0.80, "n_eval": 100.0, "support_range": 60.0}, "eval_predictions": "0" * 100}
    near = json.loads(json.dumps(base))
    near["metrics"]["accuracy"] = 0.805
    assert tp.compare(base, near)["verdict"] == "pass"
    far = json.loads(json.dumps(base))
    far["metrics"]["accuracy"] = 0.83
    assert tp.compare(base, far)["verdict"] == "fail"
    cnt = json.loads(json.dumps(base))
    cnt["metrics"]["support_range"] = 61.0
    assert tp.compare(base, cnt)["verdict"] == "fail"
    dis = json.loads(json.dumps(base))
    dis["eval_predictions"] = "0" * 98 + "11"
    assert tp.compare(base, dis)["verdict"] == "fail"           # 0.98 < 0.99


def test_missing_dataset_is_producer_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("ML_DATA_ROOT", str(tmp_path / "empty"))
    out = tmp_path / "o"
    tp.main(["--manifest", MANIFEST, "--cutoff", "2026-02-15T00:00:00Z", "--backend", "kaggle_cpu", "--out", str(out)])
    v = json.loads((out / "verdict.json").read_text())
    assert v["read_state"] == "producer_failed" and v["verdict"] == "not_applicable" and v["population"]["n"] is None


def test_ts_parsing():
    assert tp._ts_seconds("2026-01-01T00:00:00Z") == tp._ts_seconds(1767225600) == tp._ts_seconds(1767225600000)
    assert tp._ts_seconds("garbage") is None and tp._ts_seconds(None) is None
    assert not math.isnan(tp._ts_seconds("2026-01-01T00:00:00"))


def test_router_derives_the_dataset_path_from_the_manifest():
    from scripts.research import compute_router as cr
    entry = {"routing": {"published_data": "metis-market-data"}, "train_parity": {"manifest": MANIFEST}}
    assert cr.unit_dataset_paths(entry) == ["datasets-out/market_features/ETHUSDT/1h/v002/data.jsonl"]
    assert cr.published_problem(entry, cr.load_config()) is None
    # a manifest whose shard is not published, or one that does not exist, must NOT be routed off the trainer
    assert cr.published_problem({**entry, "train_parity": {"manifest": "conviction-meta-v1"}}, cr.load_config())
    assert cr.published_problem({**entry, "train_parity": {"manifest": "../nope"}}, cr.load_config())
