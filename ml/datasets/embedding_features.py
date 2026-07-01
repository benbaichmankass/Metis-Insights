"""Pretrained time-series-foundation-model (TSFM) embedding features (M19 T0.1).

The operator's framing (M19 roadmap): *"one overall model that reads everything
… slowly building capacity to understand data as much as possible."* This module
is the **cheapest, first taste** of that idea — it turns a pretrained
time-series foundation model into a **frozen feature extractor**: for each bar it
runs a small TSFM (default ``amazon/chronos-bolt-tiny``, 9M params, CPU) over the
trailing close window, mean-pools the encoder embedding, and projects it to a
fixed-width vector that a regime / conviction head can consume alongside the
hand-engineered features. The A/B question T0.1 answers: *does a learned
representation lift a boosting head vs hand-engineered features alone?* — for
$0, no labels, no training, before any in-house encoder is built.

Design docs: ``docs/research/ai-model-strategy-roadmap-2026-07-01.md`` (M19).

### Architecture — the exact sibling of ``cross_asset_features`` / ``macro_features``

**Pure functions** here compute per-bar feature rows; a thin producer script
(``scripts/ml/build_embeddings.py``) writes them to a side-stream ``data.jsonl``
that ``market_features`` as-of joins via its optional ``embedding_path`` kwarg
(``0.0`` when omitted — every existing build is unchanged). Nothing here runs on
the live money-box: T0.1 heads stay at ``candidate`` stage (offline only), so the
Chronos / torch dependency is **trainer-side only** (added to
``requirements-backtest.txt``, lazy-imported). Promotion to ``shadow`` — which
would require the same embedding computed live (train==live parity) and hence the
dep on the live VM — is a separate, operator-gated follow-up.

### Import safety + testability

This module is **stdlib-only** at import time (like ``cross_asset_features``), so
``import ml.datasets.embedding_features`` works in any environment — the column
contract, the neutral default, and the deterministic projection never need torch
or numpy. The heavy TSFM call is a **lazily-imported, injectable** ``embed_fn``:
:func:`compute_embedding_feature_rows` takes an ``embed_fn`` (default = the real
Chronos embedder, resolved lazily on first use), so the block's windowing /
stride / as-of / neutral-default logic is fully unit-testable with a stub
embedder and **no torch installed**.

### Reduction — seeded random projection (leakage-free, dimension-robust)

The raw pooled embedding is ``d_model``-wide (256 for chronos-bolt-tiny) — too
many columns to merge per bar. We reduce it to :data:`EMBEDDING_DIM` (default 32)
via a **seeded Gaussian random projection** (Johnson–Lindenstrauss). The
projection matrix is **data-independent** (derived only from ``seed`` + the input
width), so it is deterministic across processes and, crucially, **cannot leak**
future information the way a PCA fit on the whole series would. A learned /
PCA-per-fold reduction is a documented follow-up once T0.1 shows value.

### Cadence + leakage discipline

The embedding at bar ``t`` is computed from the trailing close window ending at
``t`` (``closes[t-context_len+1 .. t]``) — **past-only by construction**. The
``market_features`` forward label spans ``[t+1 .. t+forward_window_m]`` (strictly
after ``t``), so the windows never overlap. To bound the (otherwise per-bar)
Chronos cost, the producer may emit at a ``stride`` (every Nth bar); the existing
``_align_asof`` carry-forward in ``market_features`` fills the gaps with the most
recent past embedding — still leakage-safe (a bar never sees a future embedding).
``None`` / a short window / an embedder failure → ``0.0`` (neutral) at emit time,
matching the funding/OI, microstructure, macro, and cross-asset families.
"""
from __future__ import annotations

import math
import random
from typing import Any, Callable, Mapping, Sequence

# Projected embedding width — the number of columns this block contributes to
# `market_features`. 32 keeps the merged schema modest while retaining most of
# the pooled embedding's structure under a JL random projection.
EMBEDDING_DIM: int = 32

# The pretrained TSFM used as a frozen feature extractor. chronos-bolt-tiny is
# 9M params and runs sub-second on CPU — the smallest model that exposes
# encoder embeddings via `pipeline.embed()`.
EMBEDDING_MODEL_ID: str = "amazon/chronos-bolt-tiny"

# Defaults for the producer. `context_len` = trailing bars fed to the TSFM;
# `stride` = emit every Nth bar (carry-forward fills the rest, leakage-safe);
# `min_context` = fewest trailing bars before we emit (else neutral 0.0);
# `seed` = the projection seed (frozen so train==producer==live).
DEFAULT_CONTEXT_LEN: int = 64
DEFAULT_STRIDE: int = 4
DEFAULT_MIN_CONTEXT: int = 8
DEFAULT_SEED: int = 42


def _embedding_columns(out_dim: int) -> tuple[str, ...]:
    return tuple(f"tsfm_emb_{i}" for i in range(out_dim))


# The fixed embedding feature columns this family contributes to
# `market_features`. Single source of truth shared by the builder schema, the
# side-stream producer, and the tests.
EMBEDDING_FEATURE_COLUMNS: tuple[str, ...] = _embedding_columns(EMBEDDING_DIM)


def _finite_or_zero(value: float | None) -> float:
    """``None`` / non-finite → ``0.0`` (neutral) — the feature-emit shape."""
    if value is None or not math.isfinite(value):
        return 0.0
    return float(value)


# --- Deterministic, data-independent random projection (pure stdlib) ---------

# Cache of projection matrices keyed by (in_dim, out_dim, seed). The matrix is a
# function of the seed + shape ONLY (never the data), so it is reproducible
# across processes and leakage-free.
_PROJECTION_CACHE: dict[tuple[int, int, int], list[list[float]]] = {}


def projection_matrix(in_dim: int, out_dim: int, seed: int) -> list[list[float]]:
    """Seeded Gaussian random-projection matrix of shape ``(in_dim, out_dim)``.

    Entries are ``N(0, 1) / sqrt(out_dim)`` from a ``random.Random(seed)`` stream
    (Johnson–Lindenstrauss scaling). Deterministic in ``(in_dim, out_dim, seed)``
    and independent of any input data — so applying it introduces no leakage.
    """
    if in_dim <= 0 or out_dim <= 0:
        raise ValueError(f"dims must be positive; got in_dim={in_dim}, out_dim={out_dim}")
    key = (in_dim, out_dim, seed)
    cached = _PROJECTION_CACHE.get(key)
    if cached is not None:
        return cached
    rng = random.Random(seed)
    scale = 1.0 / math.sqrt(out_dim)
    matrix = [
        [rng.gauss(0.0, 1.0) * scale for _ in range(out_dim)]
        for _ in range(in_dim)
    ]
    _PROJECTION_CACHE[key] = matrix
    return matrix


def project(vec: Sequence[float], *, out_dim: int, seed: int) -> list[float]:
    """Project a raw pooled embedding to ``out_dim`` via the seeded matrix.

    A zero / all-non-finite input maps to an all-``0.0`` output (neutral). Any
    non-finite output component is coerced to ``0.0`` at emit time.
    """
    clean = [float(x) if (x is not None and math.isfinite(x)) else 0.0 for x in vec]
    matrix = projection_matrix(len(clean), out_dim, seed)
    out: list[float] = [0.0] * out_dim
    for i, xi in enumerate(clean):
        if xi == 0.0:
            continue
        row = matrix[i]
        for j in range(out_dim):
            out[j] += xi * row[j]
    return [_finite_or_zero(v) for v in out]


# --- The lazily-imported real embedder (torch/chronos, trainer-side only) -----

_PIPELINE_CACHE: dict[str, Any] = {}


def embed_available() -> bool:
    """True when the Chronos + torch deps are importable (trainer-side)."""
    try:  # pragma: no cover - depends on the optional trainer-side dep
        import importlib.util

        return (
            importlib.util.find_spec("chronos") is not None
            and importlib.util.find_spec("torch") is not None
        )
    except Exception:  # pragma: no cover - defensive
        return False


def chronos_embed_fn(model_id: str = EMBEDDING_MODEL_ID) -> Callable[[Sequence[Sequence[float]]], list[list[float]]]:
    """Build the default TSFM embedder (lazy — imports torch/chronos on first call).

    Returns a batch function ``windows -> pooled d_model vectors`` (plain lists).
    Each window is a 1-D close series; the encoder embedding is mean-pooled over
    the patch/sequence axis. Raises ``ImportError`` with an actionable message
    when the optional deps are absent — the producer script surfaces that; the
    dataset build without ``embedding_path`` never calls this.
    """

    def _embed(windows: Sequence[Sequence[float]]) -> list[list[float]]:
        try:  # pragma: no cover - exercised on the trainer VM, not in CI
            import torch
            from chronos import BaseChronosPipeline
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "TSFM embeddings need the optional trainer-side deps "
                "(`pip install -r requirements-backtest.txt` → chronos-forecasting + torch). "
                f"Original error: {exc}"
            ) from exc

        pipe = _PIPELINE_CACHE.get(model_id)
        if pipe is None:  # pragma: no cover
            pipe = BaseChronosPipeline.from_pretrained(
                model_id, device_map="cpu", torch_dtype=torch.float32
            )
            _PIPELINE_CACHE[model_id] = pipe

        contexts = [torch.tensor(list(w), dtype=torch.float32) for w in windows]
        # `embed` returns (embeddings, loc_scale); embeddings is
        # (batch, seq_patches, d_model). Mean-pool over the patch axis.
        embeddings, _ = pipe.embed(contexts)  # pragma: no cover
        pooled = embeddings.to(torch.float32).mean(dim=1)  # pragma: no cover
        return [row.tolist() for row in pooled]  # pragma: no cover

    return _embed


# --- The pure producer (fully testable with an injected embed_fn) -------------

def _strided_indices(n: int, stride: int) -> list[int]:
    """Bar indices to emit an embedding at: every ``stride``-th bar + the last.

    The as-of carry-forward in ``market_features`` fills the gaps with the most
    recent PAST embedding, so a stride never introduces leakage — it only
    trades resolution for build cost.
    """
    if stride < 1:
        raise ValueError(f"stride must be >= 1; got {stride}")
    idx = list(range(0, n, stride))
    if n > 0 and idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def compute_embedding_feature_rows(
    target_rows: Sequence[Mapping[str, Any]],
    *,
    context_len: int = DEFAULT_CONTEXT_LEN,
    stride: int = DEFAULT_STRIDE,
    out_dim: int = EMBEDDING_DIM,
    seed: int = DEFAULT_SEED,
    min_context: int = DEFAULT_MIN_CONTEXT,
    embed_fn: Callable[[Sequence[Sequence[float]]], list[list[float]]] | None = None,
    batch_size: int = 256,
) -> list[dict[str, Any]]:
    """Per-bar TSFM embedding feature rows for a target, keyed at the bar's ts.

    ``target_rows`` are ``market_raw``-shaped (``{ts, close, ...}``). For each
    emitted (strided) bar with at least ``min_context`` trailing closes, the
    embedder is run over the trailing ``context_len`` window ending AT that bar
    (past-only), mean-pooled, and projected to ``out_dim`` columns
    (``tsfm_emb_0 .. tsfm_emb_{out_dim-1}``). Bars with too little history emit
    no row (the as-of join leaves them at the neutral ``0.0`` default).

    ``embed_fn`` defaults to the real Chronos embedder (lazy); tests inject a
    stub so the windowing / stride / projection logic runs without torch. Any
    embedder failure on a batch degrades that batch's rows to neutral ``0.0``
    rather than aborting the build — a missing representation must never crash a
    dataset build.
    """
    if context_len < 1:
        raise ValueError(f"context_len must be >= 1; got {context_len}")
    if out_dim < 1:
        raise ValueError(f"out_dim must be >= 1; got {out_dim}")
    if min_context < 1:
        raise ValueError(f"min_context must be >= 1; got {min_context}")

    tgt = sorted(target_rows, key=lambda r: str(r.get("ts", "")))
    n = len(tgt)
    if n == 0:
        return []
    bar_ts = [str(r.get("ts", "")) for r in tgt]
    closes = [
        float(r["close"]) if r.get("close") is not None else None for r in tgt
    ]

    if embed_fn is None:
        embed_fn = chronos_embed_fn()

    emit_idx = [i for i in _strided_indices(n, stride) if i + 1 >= min_context]
    cols = _embedding_columns(out_dim)
    out_rows: list[dict[str, Any]] = []

    for start in range(0, len(emit_idx), batch_size):
        batch_idx = emit_idx[start : start + batch_size]
        windows: list[list[float]] = []
        for i in batch_idx:
            lo = max(0, i - context_len + 1)
            # Drop non-positive / None closes from the window (defensive); a
            # window that collapses below min_context after cleaning is emitted
            # neutral.
            w = [c for c in closes[lo : i + 1] if c is not None and c > 0]
            windows.append(w)

        try:
            raw = embed_fn(windows)
        except Exception:
            raw = None

        for k, i in enumerate(batch_idx):
            vec = None
            if raw is not None and k < len(raw) and len(windows[k]) >= min_context:
                vec = raw[k]
            projected = (
                project(vec, out_dim=out_dim, seed=seed)
                if vec is not None
                else [0.0] * out_dim
            )
            row: dict[str, Any] = {"ts": bar_ts[i]}
            for j, col in enumerate(cols):
                row[col] = _finite_or_zero(projected[j])
            out_rows.append(row)

    return out_rows
