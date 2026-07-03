"""Tests for the SSL corpus-encoder embedding wiring into market_features (M19 T1.2 P2).

All tests run WITHOUT onnxruntime/torch — the encoder embedder is injected as a
stub (mirroring the block's design: the real ONNX call is lazy + injectable so the
windowing / one-day-lag / as-of / neutral-default logic is CI-testable). The
market_features integration builds a synthetic `corpus_emb_*` side-stream directly.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from ml.datasets.corpus_embedding_features import (
    CORPUS_EMBEDDING_DIM,
    CORPUS_EMBEDDING_FEATURE_COLUMNS,
    compute_corpus_embedding_rows,
    corpus_embedding_sidestream,
)


def _stage_market_raw(tmp_path: Path, closes: list[float]) -> Path:
    base = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    root = tmp_path / "market_raw" / "BTCUSDT" / "1h" / "v001"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "data.jsonl").open("w", encoding="utf-8") as fh:
        for i, c in enumerate(closes):
            ts = (base + timedelta(hours=i)).isoformat().replace("+00:00", "Z")
            fh.write(json.dumps({
                "ts": ts, "symbol": "BTCUSDT", "timeframe": "1h",
                "open": c, "high": c * 1.001, "low": c * 0.999,
                "close": c, "volume": 100.0, "source": "test",
            }) + "\n")
    return root


def _stage_corpus_embedding_sidestream(tmp_path: Path) -> Path:
    """A tiny `ts`-keyed `corpus_emb_*` side-stream (two daily embeddings).

    Built through the real `corpus_embedding_sidestream` (one-day-lag re-keying)
    so the join under test is exercised end-to-end. Panel date 2026-01-01 → ts
    2026-01-02T00:00:00Z (value 1.0); 2026-01-02 → 2026-01-03T00:00:00Z (2.0).
    """
    emb_rows = [
        {"date": "2026-01-01", **{c: 1.0 for c in CORPUS_EMBEDDING_FEATURE_COLUMNS}},
        {"date": "2026-01-02", **{c: 2.0 for c in CORPUS_EMBEDDING_FEATURE_COLUMNS}},
    ]
    sidestream = corpus_embedding_sidestream(emb_rows, lag_days=1)
    out = tmp_path / "corpus_embeddings" / "all" / "daily" / "v001"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "data.jsonl").open("w", encoding="utf-8") as fh:
        for r in sidestream:
            fh.write(json.dumps(r) + "\n")
    return out


def _closes(n: int = 60) -> list[float]:
    # Mildly varying closes so the base regime pipeline emits complete rows.
    return [100.0 + (i % 7) - (i % 3) * 0.5 + i * 0.1 for i in range(n)]


class TestMarketFeaturesIntegration:
    def test_without_corpus_embedding_path_columns_are_zero(self, tmp_path):
        from ml.datasets.families.market_features import MarketFeaturesBuilder

        mr = _stage_market_raw(tmp_path, _closes())
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=mr, vol_window_n=5, forward_window_m=3,
        ))
        assert rows, "baseline build should emit rows"
        for r in rows:
            for c in CORPUS_EMBEDDING_FEATURE_COLUMNS:
                assert r[c] == 0.0

    def test_with_corpus_embedding_path_columns_are_asof_carried(self, tmp_path):
        from ml.datasets.families.market_features import MarketFeaturesBuilder

        mr = _stage_market_raw(tmp_path, _closes())
        side = _stage_corpus_embedding_sidestream(tmp_path)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=mr, vol_window_n=5, forward_window_m=3,
            corpus_embedding_path=side,
        ))
        assert rows
        # Every corpus embedding column present on every row (schema completeness).
        for r in rows:
            for c in CORPUS_EMBEDDING_FEATURE_COLUMNS:
                assert c in r
        # Bars before the first side-stream ts (2026-01-02T00:00:00Z) carry the
        # neutral 0.0 default; bars at/after it carry the as-of value.
        first_ts = "2026-01-02T00:00:00Z"
        before = [r for r in rows if r["ts"] < first_ts]
        after = [r for r in rows if r["ts"] >= first_ts]
        assert before and after, "fixture must straddle the first side-stream ts"
        assert all(r["corpus_emb_0"] == 0.0 for r in before)
        assert all(r["corpus_emb_0"] != 0.0 for r in after)


class TestPureProducerPath:
    """The onnxruntime-free producer path: compute rows + re-key the side-stream."""

    def _panel(self, n: int = 40) -> list[dict]:
        base = datetime.fromisoformat("2026-01-01").date()
        return [
            {
                "date": (base + timedelta(days=i)).isoformat(),
                "values": {"s0": float(i), "s1": float(i) * 0.5},
            }
            for i in range(n)
        ]

    def _stub_embed(self):
        # window -> vector; deterministic, content-dependent on the window length.
        def _embed(window):
            return [float(len(window))] * CORPUS_EMBEDDING_DIM

        return _embed

    def test_compute_then_sidestream_shapes(self):
        panel = self._panel(40)
        emb_rows = compute_corpus_embedding_rows(
            panel, embed_fn=self._stub_embed(), seq_len=8,
        )
        assert emb_rows, "rows emitted once min_context is met"
        for r in emb_rows:
            assert set(r) == {"date", *CORPUS_EMBEDDING_FEATURE_COLUMNS}

        side = corpus_embedding_sidestream(emb_rows, lag_days=1)
        assert len(side) == len(emb_rows)
        for r in side:
            assert set(r) == {"ts", *CORPUS_EMBEDDING_FEATURE_COLUMNS}
            assert r["ts"].endswith("T00:00:00Z")
        # ts ascending (sidestream sorts) and one-day-lagged past each date.
        assert side == sorted(side, key=lambda x: x["ts"])

    def test_embedder_failure_degrades_to_no_row(self):
        def _boom(window):
            raise RuntimeError("no onnxruntime on this box")

        emb_rows = compute_corpus_embedding_rows(
            self._panel(40), embed_fn=_boom, seq_len=8,
        )
        assert emb_rows == [], "an embedder failure emits no row (neutral 0.0 join)"


class TestManifestParses:
    def test_corpusemb_manifest_parses_and_resolves(self):
        from ml.experiments.runner import _resolve_callable
        from ml.manifest import TrainingManifest

        path = (
            Path(__file__).resolve().parents[2]
            / "ml" / "configs" / "btc-regime-15m-lgbm-corpusemb-pcv-v1.yaml"
        )
        m = TrainingManifest.from_yaml(path)
        assert m.model_id == "btc-regime-15m-lgbm-corpusemb-pcv-v1"
        assert m.dataset.family == "market_features"
        assert m.target_deployment_stage == "candidate"
        # The feature set carries the base cols + the 16 corpus_emb_* cols.
        feats = m.trainer_config["feature_columns"]
        for c in CORPUS_EMBEDDING_FEATURE_COLUMNS:
            assert c in feats
        # Trainer + evaluator qualnames resolve to importable callables.
        assert _resolve_callable(m.trainer) is not None
        assert _resolve_callable(m.evaluator) is not None
