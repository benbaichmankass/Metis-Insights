"""Tests for the pure pretrained-TSFM embedding feature block (M19 T0.1).

All tests run WITHOUT torch/chronos — the heavy embedder is injected as a stub,
mirroring the block's design (the real Chronos call is lazy + injectable so the
windowing / stride / as-of / neutral-default logic is CI-testable).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from ml.datasets.embedding_features import (
    EMBEDDING_DIM,
    EMBEDDING_FEATURE_COLUMNS,
    _finite_or_zero,
    _strided_indices,
    compute_embedding_feature_rows,
    project,
    projection_matrix,
)


def _bar_rows(n: int, *, base_close: float = 100.0) -> list[dict]:
    base = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    return [
        {
            "ts": (base + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
            "symbol": "BTCUSDT",
            "close": base_close + i,
        }
        for i in range(n)
    ]


def _stub_embed(d_model: int = 8):
    """Deterministic batch embedder: pooled vec = [len(window)] * d_model."""

    def _embed(windows):
        return [[float(len(w))] * d_model for w in windows]

    return _embed


class TestColumnContract:
    def test_columns_match_dim_and_naming(self):
        assert EMBEDDING_FEATURE_COLUMNS == tuple(
            f"tsfm_emb_{i}" for i in range(EMBEDDING_DIM)
        )
        assert len(EMBEDDING_FEATURE_COLUMNS) == EMBEDDING_DIM

    def test_finite_or_zero(self):
        assert _finite_or_zero(None) == 0.0
        assert _finite_or_zero(float("nan")) == 0.0
        assert _finite_or_zero(float("inf")) == 0.0
        assert _finite_or_zero(1.5) == 1.5


class TestProjection:
    def test_deterministic_and_shape(self):
        v = [0.1 * i for i in range(256)]
        p1 = project(v, out_dim=32, seed=42)
        p2 = project(v, out_dim=32, seed=42)
        assert p1 == p2
        assert len(p1) == 32

    def test_data_independent_matrix_is_reproducible(self):
        # Same (in_dim, out_dim, seed) → identical matrix (no data dependence →
        # no leakage). Different seed → different matrix.
        m1 = projection_matrix(16, 8, 7)
        m2 = projection_matrix(16, 8, 7)
        m3 = projection_matrix(16, 8, 99)
        assert m1 == m2
        assert m1 != m3

    def test_zero_and_nonfinite_input_map_to_zero(self):
        assert project([0.0] * 64, out_dim=32, seed=1) == [0.0] * 32
        assert project([float("nan")] * 64, out_dim=32, seed=1) == [0.0] * 32

    def test_nonzero_input_yields_nonzero_projection(self):
        out = project([1.0] * 64, out_dim=32, seed=3)
        assert any(x != 0.0 for x in out)


class TestStridedIndices:
    def test_includes_last_bar(self):
        assert _strided_indices(20, 4) == [0, 4, 8, 12, 16, 19]

    def test_stride_one_is_every_bar(self):
        assert _strided_indices(5, 1) == [0, 1, 2, 3, 4]


class TestComputeEmbeddingRows:
    def test_emits_at_strided_indices_meeting_min_context(self):
        rows = _bar_rows(20)
        out = compute_embedding_feature_rows(
            rows, context_len=8, stride=4, min_context=8, embed_fn=_stub_embed()
        )
        # Strided indices {0,4,8,12,16,19}; only those with i+1>=8 emit → {8,12,16,19}.
        assert [r["ts"] for r in out] == [rows[i]["ts"] for i in (8, 12, 16, 19)]

    def test_row_has_ts_plus_fixed_width_embedding(self):
        out = compute_embedding_feature_rows(
            _bar_rows(20), context_len=8, stride=4, min_context=8, embed_fn=_stub_embed()
        )
        for r in out:
            assert set(r) == {"ts", *EMBEDDING_FEATURE_COLUMNS}
            assert len(r) == 1 + EMBEDDING_DIM

    def test_deterministic(self):
        rows = _bar_rows(20)
        a = compute_embedding_feature_rows(
            rows, context_len=8, stride=4, min_context=8, embed_fn=_stub_embed()
        )
        b = compute_embedding_feature_rows(
            rows, context_len=8, stride=4, min_context=8, embed_fn=_stub_embed()
        )
        assert a == b

    def test_embedder_failure_degrades_to_neutral_zeros(self):
        def _boom(windows):
            raise RuntimeError("no gpu on this box")

        out = compute_embedding_feature_rows(
            _bar_rows(20), context_len=8, stride=4, min_context=8, embed_fn=_boom
        )
        assert out, "rows are still emitted (neutral) on embedder failure"
        assert all(
            all(r[c] == 0.0 for c in EMBEDDING_FEATURE_COLUMNS) for r in out
        )

    def test_past_only_window_never_reaches_the_future(self):
        # The stub returns the window length. A window ending at bar i uses
        # closes[max(0,i-context_len+1)..i], so the reported length can never
        # exceed min(context_len, i+1) — proof the window is past-only.
        rows = _bar_rows(30)
        out = compute_embedding_feature_rows(
            rows, context_len=8, stride=1, min_context=8,
            embed_fn=lambda ws: [[float(len(w))] for w in ws], out_dim=1,
        )
        by_ts = {r["ts"]: r for r in out}
        for i, row in enumerate(rows):
            r = by_ts.get(row["ts"])
            if r is None:
                continue
            # projection of [len] is deterministic; recover sign-free magnitude
            # by re-projecting the same scalar and comparing.
            expected_len = float(min(8, i + 1))
            assert r["tsfm_emb_0"] == project([expected_len], out_dim=1, seed=42)[0]

    def test_empty_input(self):
        assert compute_embedding_feature_rows([], embed_fn=_stub_embed()) == []


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


def _stage_embedding_sidestream(tmp_path: Path, market_raw: Path) -> Path:
    """Produce the tsfm_emb side-stream from the staged market_raw via the stub."""
    rows = [
        json.loads(line)
        for line in (market_raw / "data.jsonl").read_text().splitlines()
    ]
    emb_rows = compute_embedding_feature_rows(
        rows, context_len=8, stride=4, min_context=8, embed_fn=_stub_embed()
    )
    out = tmp_path / "embeddings" / "BTCUSDT" / "1h" / "v001"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "data.jsonl").open("w", encoding="utf-8") as fh:
        for r in emb_rows:
            fh.write(json.dumps(r) + "\n")
    return out


class TestMarketFeaturesIntegration:
    def _closes(self, n: int = 60) -> list[float]:
        # Mildly varying closes so the base regime pipeline emits complete rows.
        return [100.0 + (i % 7) - (i % 3) * 0.5 + i * 0.1 for i in range(n)]

    def test_without_embedding_path_columns_are_zero(self, tmp_path):
        from ml.datasets.families.market_features import MarketFeaturesBuilder

        mr = _stage_market_raw(tmp_path, self._closes())
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=mr, vol_window_n=5, forward_window_m=3,
        ))
        assert rows, "baseline build should emit rows"
        for r in rows:
            for c in EMBEDDING_FEATURE_COLUMNS:
                assert r[c] == 0.0

    def test_with_embedding_path_columns_are_asof_carried(self, tmp_path):
        from ml.datasets.families.market_features import MarketFeaturesBuilder

        closes = self._closes()
        mr = _stage_market_raw(tmp_path, closes)
        emb = _stage_embedding_sidestream(tmp_path, mr)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=mr, vol_window_n=5, forward_window_m=3,
            embedding_path=emb,
        ))
        assert rows
        # At least one emitted market_features row must carry a non-zero
        # embedding (as-of carried from the side-stream) — proof the merge works.
        assert any(
            any(r[c] != 0.0 for c in EMBEDDING_FEATURE_COLUMNS) for r in rows
        )
        # Every embedding column present on every row (schema completeness).
        for r in rows:
            for c in EMBEDDING_FEATURE_COLUMNS:
                assert c in r


class TestReduction:
    """M19 T0.1 follow-up — PCA vs random reduction of the raw pooled embedding."""

    def _rows(self, n=60):
        return _bar_rows(n)

    def test_invalid_reduction_raises(self):
        import pytest

        with pytest.raises(ValueError):
            compute_embedding_feature_rows(
                self._rows(20), embed_fn=_stub_embed(), reduction="nope"
            )

    def test_invalid_pca_fit_frac_raises(self):
        import pytest

        for bad in (0.0, 1.5, -0.1):
            with pytest.raises(ValueError):
                compute_embedding_feature_rows(
                    self._rows(20), embed_fn=_stub_embed(),
                    reduction="pca", pca_fit_frac=bad,
                )

    def test_random_reduction_is_the_default(self):
        rows = self._rows(30)
        a = compute_embedding_feature_rows(
            rows, context_len=8, stride=4, min_context=8, embed_fn=_stub_embed()
        )
        b = compute_embedding_feature_rows(
            rows, context_len=8, stride=4, min_context=8, embed_fn=_stub_embed(),
            reduction="random",
        )
        assert a == b

    def test_pca_reduction_shape_and_determinism(self):
        import pytest

        pytest.importorskip("numpy")
        # A varying stub embedder (per-window content) so PCA has real structure.
        def _varying(windows):
            out = []
            for w in windows:
                s = float(sum(w))
                out.append([s, s * 0.5, -s, len(w) * 1.0, s * s * 1e-3, 1.0, 2.0, 3.0])
            return out

        rows = self._rows(80)
        kw = dict(context_len=8, stride=2, min_context=8, out_dim=4,
                  embed_fn=_varying, reduction="pca", pca_fit_frac=0.5)
        a = compute_embedding_feature_rows(rows, **kw)
        b = compute_embedding_feature_rows(rows, **kw)
        assert a == b, "PCA reduction must be deterministic"
        assert a, "PCA reduction should emit rows"
        cols = tuple(f"tsfm_emb_{i}" for i in range(4))
        for r in a:
            assert set(r) == {"ts", *cols}
        # PCA on a non-degenerate varying signal should yield some non-zero output.
        assert any(any(r[c] != 0.0 for c in cols) for r in a)

    def test_pca_embedder_failure_is_neutral(self):
        import pytest

        pytest.importorskip("numpy")

        def _boom(windows):
            raise RuntimeError("no torch")

        out = compute_embedding_feature_rows(
            self._rows(30), context_len=8, stride=4, min_context=8,
            embed_fn=_boom, reduction="pca",
        )
        assert out
        assert all(
            all(r[c] == 0.0 for c in EMBEDDING_FEATURE_COLUMNS) for r in out
        )
