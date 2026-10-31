"""leg_corpus family (ML-LINE-S1-CORPUS): pooled by family, leg as a feature, bad legs skipped not pooled."""
from __future__ import annotations

import json
from pathlib import Path

from ml.datasets.families.leg_corpus import FEATURE_COLUMNS, LegCorpusBuilder
from ml.datasets.registry import get_builder


def _leg(root: Path, fam: str, leg: str, sym: str, *, state="ok", tf_equal=True, n=3) -> None:
    d = root / fam / leg
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"leg": leg, "symbol": sym, "config_timeframe": "4h", "state": state,
                                             "timeframe_equal": tf_equal, "generated_at": "2026-10-31T00:00:00Z"}))
    rows = [{"entry_time": f"2026-01-0{i+1}T00:00:00Z", "direction": "long" if i % 2 else "short", "net_r": 0.5 * i - 0.2,
             "gross_r": 0.5 * i, "mfe_r": 1.0, "cost_fee_r": 0.1, "cost_slippage_r": 0.05, "cost_funding_r": None,
             "confidence": 0.7, "exit_reason": "tp"} for i in range(n)]
    rows.append({"entry_time": "x", "direction": "long", "net_r": None})  # unlabeled -> dropped
    (d / "trades.jsonl").write_text("\n".join(json.dumps(r) for r in rows))


def test_pooled_by_family_with_leg_feature_and_skips(tmp_path):
    _leg(tmp_path, "trend_4h", "eth_4h", "ETHUSDT")
    _leg(tmp_path, "trend_4h", "xrp_4h", "XRPUSDT")
    _leg(tmp_path, "trend_4h", "bad_tf", "SOLUSDT", tf_equal=False)      # the mislabelled-timeframe defect
    _leg(tmp_path, "trend_4h", "failed", "ADAUSDT", state="producer_failed")
    _leg(tmp_path, "pullback_1d", "iaum", "IAUM", n=2)
    b = LegCorpusBuilder()
    rows = list(b.iter_rows(corpus_dir=tmp_path))
    assert {r["leg"] for r in rows} == {"eth_4h", "xrp_4h", "iaum"}
    assert sum(r["corpus_family"] == "trend_4h" for r in rows) == 6 and len(rows) == 8
    assert set(b.skipped) == {"bad_tf", "failed"}
    assert all(r["cost_funding_r"] is None for r in rows)               # missing stays None, never 0
    assert {r["is_long"] for r in rows} == {0, 1}
    only = list(LegCorpusBuilder().iter_rows(corpus_dir=tmp_path, families="pullback_1d"))
    assert {r["leg"] for r in only} == {"iaum"}


def test_registered_and_outcomes_are_not_features(tmp_path):
    assert isinstance(get_builder("leg_corpus"), LegCorpusBuilder)
    assert not {"net_r", "gross_r", "mfe_r", "exit_reason", "exit_time"} & set(FEATURE_COLUMNS)
    assert {"leg", "symbol"} <= set(FEATURE_COLUMNS)
    _leg(tmp_path, "trend_4h", "eth_4h", "ETHUSDT")
    out = tmp_path / "ds"
    b = LegCorpusBuilder()
    paths = b.build(out, "v1", source="unit-test", corpus_dir=tmp_path)
    assert paths.data.is_file() and len(paths.data.read_text().splitlines()) == 3
