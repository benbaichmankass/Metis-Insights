"""SSL corpus-encoder embedding features — the offline side-stream block (M19 T1.2 P1).

The sibling of :mod:`ml.datasets.embedding_features` (T0.1), sourced from the
in-house **masked-reconstruction corpus encoder**
(:class:`ml.trainers.ssl_corpus_encoder.SSLCorpusEncoderTrainer`) instead of a
frozen Chronos TSFM. It runs the exported encoder over the historical daily
``corpus_panel`` and emits a per-day ``corpus_emb_0 .. corpus_emb_{d-1}``
side-stream that ``market_features`` can as-of join **one-day-lagged**
(``0.0`` when absent), feeding the LightGBM heads a learned, breadth-aware
market-state vector. Design: ``docs/research/T1.2-ssl-encoder-DESIGN.md`` §5.

### Architecture — the exact sibling of ``embedding_features`` / ``macro_features``

**Pure functions** here compute the per-day embedding rows via an **injectable**
``embed_fn`` (default = the real onnxruntime encoder, resolved lazily from a
``model_state``); a thin producer script (T1.2 P2, not in this PR) writes them to a
side-stream ``data.jsonl`` that ``market_features`` as-of joins via a new
``corpus_embedding_path`` kwarg (``0.0`` when omitted). Nothing here runs on the
live money-box: T1.2 heads stay at ``candidate`` stage (offline only), so the
onnxruntime dep is trainer-side only and lazy-imported.

### Import safety + testability

This module is **stdlib-only** at import time (like ``embedding_features``), so
``import ml.datasets.corpus_embedding_features`` works in any environment. The
heavy encoder call is the lazily-resolved, injectable ``embed_fn``, so the
windowing / stride / as-of / one-day-lag / neutral-default logic is fully
unit-testable with a stub embedder and **no torch/onnxruntime installed**.

### Cadence + leakage discipline

The embedding for panel date ``G`` is computed from the window of the last ``L``
panel rows ending at ``G`` — **past-only by construction**, and the panel itself is
already strictly-prior / one-day-lagged / forward-filled / no-backfill. The block
emits keyed by that date; :func:`corpus_embedding_sidestream` re-keys each row to
``date + lag_days`` (default one calendar day) so the standard ``market_features``
``_align_asof`` (``ts <=`` carry-forward) yields a clean **one-day lag** onto the
intraday bars — a slow *context* feature (regime/direction conditioning), joined
as-of to intraday bars, never a fast intraday signal. An encoder failure on a day
degrades that day's row to neutral (no row emitted → the as-of join leaves the
neutral ``0.0`` default), never a fabricated embedding.
"""
from __future__ import annotations

import math
from datetime import date as _date
from datetime import timedelta
from typing import Any, Callable, Mapping, Sequence

# Projected embedding width — the number of `corpus_emb_*` columns this block
# contributes to `market_features`. MUST equal the trained encoder's
# `embedding_dim` (default 16); the producer reads it from the model_state.
CORPUS_EMBEDDING_DIM: int = 16

# The panel-window length the encoder was trained with (its `seq_len`). The
# producer reads the real value from the model_state; this is the block default.
DEFAULT_SEQ_LEN: int = 64

# Default calendar lag applied when re-keying the day-embedding onto the bar grid
# (one trading day of slack, so an intraday bar never sees its own day's panel
# embedding). Belt-and-suspenders on top of the panel's own one-day-lag.
DEFAULT_LAG_DAYS: int = 1


def corpus_embedding_columns(out_dim: int) -> tuple[str, ...]:
    return tuple(f"corpus_emb_{i}" for i in range(out_dim))


# The fixed feature columns this family contributes to `market_features`. Single
# source of truth shared by the (future P2) builder schema, the producer, and the
# tests.
CORPUS_EMBEDDING_FEATURE_COLUMNS: tuple[str, ...] = corpus_embedding_columns(
    CORPUS_EMBEDDING_DIM
)


def _finite_or_zero(value: float | None) -> float:
    """``None`` / non-finite → ``0.0`` (neutral) — the feature-emit shape."""
    if value is None or not math.isfinite(value):
        return 0.0
    return float(value)


# --- The pure producer (fully testable with an injected embed_fn) ------------ #
def compute_corpus_embedding_rows(
    panel_rows: Sequence[Mapping[str, Any]],
    *,
    embed_fn: Callable[[Sequence[Mapping[str, Any]]], Sequence[float]],
    seq_len: int = DEFAULT_SEQ_LEN,
    out_dim: int = CORPUS_EMBEDDING_DIM,
    min_context: int | None = None,
) -> list[dict[str, Any]]:
    """Per-day corpus-encoder embedding rows, keyed by the panel ``date``.

    For each panel date ``G`` with at least ``min_context`` trailing rows (default
    ``seq_len`` — a full window, matching the trainer's drop-incomplete rule), the
    ``embed_fn`` is run over the window of the last ``seq_len`` rows ending AT ``G``
    (past-only), producing a ``out_dim``-vector emitted as
    ``{date: G, corpus_emb_0.., ...}``. ``embed_fn`` is the encoder embedder
    (``window_rows -> list[float]``); tests inject a stub so the windowing runs
    without torch/onnxruntime. Any embedder failure on a day degrades that day to
    **no row** (the as-of join then leaves the neutral ``0.0`` default) rather than
    aborting the build or fabricating an embedding.
    """
    if seq_len < 1:
        raise ValueError(f"seq_len must be >= 1; got {seq_len}")
    if out_dim < 1:
        raise ValueError(f"out_dim must be >= 1; got {out_dim}")
    ctx = seq_len if min_context is None else int(min_context)
    if ctx < 1:
        raise ValueError(f"min_context must be >= 1; got {ctx}")

    ordered = sorted(panel_rows, key=lambda r: str(r.get("date", "")))
    n = len(ordered)
    cols = corpus_embedding_columns(out_dim)
    out_rows: list[dict[str, Any]] = []
    for i in range(n):
        if i + 1 < ctx:
            continue  # too little history — leave neutral (no row)
        lo = max(0, i - seq_len + 1)
        window = ordered[lo : i + 1]
        try:
            vec = list(embed_fn(window))
        except Exception:
            vec = []
        if len(vec) < out_dim:
            continue  # embedder miss → neutral (no row), never a fabricated vector
        row: dict[str, Any] = {"date": str(ordered[i].get("date", ""))}
        for j, col in enumerate(cols):
            row[col] = _finite_or_zero(vec[j])
        out_rows.append(row)
    return out_rows


# --- The `*_path`-style side-stream + as-of join (P2 market_features wiring) -- #
def corpus_embedding_sidestream(
    emb_rows: Sequence[Mapping[str, Any]],
    *,
    lag_days: int = DEFAULT_LAG_DAYS,
    columns: Sequence[str] = CORPUS_EMBEDDING_FEATURE_COLUMNS,
) -> list[dict[str, Any]]:
    """Re-key day-embedding rows to a ``ts``-keyed side-stream, one-day-lagged.

    Each ``{date, corpus_emb_*}`` row becomes ``{ts: <date + lag_days>T00:00:00Z,
    corpus_emb_*}`` — so the standard ``market_features`` ``_align_asof`` (``ts <=
    bar_ts`` carry-forward) hands an intraday bar on calendar day ``D`` the
    embedding of panel day ``≤ D - lag_days``. That is the whole P2 wiring: point
    ``market_features(corpus_embedding_path=...)`` at a ``data.jsonl`` of these
    rows and it joins them exactly like the T0.1 ``embedding_path`` block. Rows
    with an unparseable ``date`` are dropped (never mis-keyed).
    """
    if lag_days < 0:
        raise ValueError(f"lag_days must be >= 0; got {lag_days}")
    out: list[dict[str, Any]] = []
    for r in emb_rows:
        raw_date = str(r.get("date", ""))[:10]
        try:
            d = _date.fromisoformat(raw_date)
        except ValueError:
            continue
        ts = (d + timedelta(days=lag_days)).isoformat() + "T00:00:00Z"
        row: dict[str, Any] = {"ts": ts}
        for col in columns:
            row[col] = _finite_or_zero(
                r.get(col) if isinstance(r.get(col), (int, float)) else None
            )
        out.append(row)
    out.sort(key=lambda x: x["ts"])
    return out


def align_corpus_embeddings(
    bar_ts: Sequence[str],
    sidestream_rows: Sequence[Mapping[str, Any]],
    *,
    columns: Sequence[str] = CORPUS_EMBEDDING_FEATURE_COLUMNS,
) -> dict[str, list[float]]:
    """As-of (past-only) carry-forward of a ``ts``-keyed embedding side-stream.

    ``out[col][i]`` is the most recent ``sidestream`` value of ``col`` whose ``ts``
    is ``<= bar_ts[i]`` (carry-forward; ``0.0`` until the first observation at/before
    the bar). Mirrors ``market_features._align_asof`` + ``_finite_or_zero`` so the
    standalone join is byte-identical to what the P2-wired family would do — no need
    to edit ``market_features`` to test the join. Both inputs must be ascending in
    ``ts``; feed :func:`corpus_embedding_sidestream` output (already sorted) so the
    one-day lag is baked into the ``ts`` key.
    """
    ordered = sorted(sidestream_rows, key=lambda r: str(r.get("ts", "")))
    m = len(ordered)
    out: dict[str, list[float]] = {col: [0.0] * len(bar_ts) for col in columns}
    last: dict[str, float] = {col: 0.0 for col in columns}
    j = 0
    for i, bts in enumerate(bar_ts):
        while j < m and str(ordered[j].get("ts", "")) <= str(bts):
            for col in columns:
                val = ordered[j].get(col)
                if isinstance(val, (int, float)) and math.isfinite(float(val)):
                    last[col] = float(val)
            j += 1
        for col in columns:
            out[col][i] = last[col]
    return out


# --- The real (lazily-resolved) encoder embedder -----------------------------#
def predictor_embed_fn(
    model_state: Mapping[str, Any],
) -> Callable[[Sequence[Mapping[str, Any]]], list[float]]:
    """Build the real corpus-encoder embedder from a trained ``model_state``.

    Returns ``window_rows -> list[float]`` backed by the ONNX encoder served on CPU
    via :class:`ml.predictors.ssl_corpus_encoder.SSLCorpusEncoderPredictor` — lazy,
    so onnxruntime/numpy are only touched on the trainer side when the producer runs
    (never at module import, never on the money-box). Tests inject a stub instead.
    """
    from ..predictors.ssl_corpus_encoder import SSLCorpusEncoderPredictor  # noqa: PLC0415

    predictor = SSLCorpusEncoderPredictor(model_state)

    def _embed(window_rows: Sequence[Mapping[str, Any]]) -> list[float]:
        return predictor.embed(window_rows)

    return _embed
