"""SSL corpus-encoder trainer — masked-reconstruction (MAE-style) autoencoder (M19 T1.2 P1).

The first **self-supervised** trainer: a small masked-reconstruction (MAE-style)
autoencoder over the wide, leakage-safe daily ``corpus_panel`` (the aligned
date×series matrix of rates / VIX / equity / commodity / credit / FX context). It
learns a market-state **embedding** from the abundant *unlabeled* corpus — the one
M19 lever that is not bound by the ~350-real-trade label ceiling — to feed the
downstream boosting heads as a task-tailored, breadth-aware feature (the T0.1
embedding pattern, but learned in-house over cross-asset context instead of a
frozen generic TSFM). Design: ``docs/research/T1.2-ssl-encoder-DESIGN.md`` §4/§5/§7.

Design contract (mirrors the T1.1 torch trainer verbatim where it can):

- **GPU-train, CPU-serve.** ``fit`` trains on CUDA when available (else CPU;
  identical math) and returns a ``model_state`` whose served artifact is an
  **ONNX graph** (base64) of the **encoder trunk only** — the decoder is
  discarded. The predictor serves it via ``onnxruntime`` on the CPU money-box.
  **No torch on the live box.**
- **Fail-closed parity.** Before returning, the encoder is ONNX-exported and its
  CPU-ONNX output is verified against the torch output
  (:func:`ml.trainers.onnx_export.export_and_verify`, reused verbatim). A parity
  miss raises — an encoder that can't be reproduced on the serve target is never
  emitted.
- **Standardization baked into the graph.** The per-cell mean/std (frozen from the
  **train** windows only) is the encoder's first op, so the served ONNX consumes
  the RAW value+mask serve input and parity covers normalization too — the
  predictor does no arithmetic of its own.
- **Missingness is never a fabricated zero.** The panel's ``None`` cells (a series
  before its start) carry a parallel ``L×F`` **mask channel** (``mask=1`` ⟺
  absent); the encoder sees value 0 there but the mask tells it the cell is absent.
  The same mask channel carries the MAE random-masking: the reconstruction loss is
  taken **only** over artificially-masked-but-observed cells, never over natively
  missing ones.
- **Torch is lazy.** ``torch`` / ``numpy`` and the module classes are imported /
  defined inside ``fit``, so this module imports on a torch-free environment
  (paired predictor + the money-box). Same discipline as the LightGBM/TCN trainers.

Input rows are ``corpus_panel``-family rows ``{"date": "YYYY-MM-DD", "values":
{series_id: float|None}}`` — an aligned, leakage-safe (strictly-prior as-of,
one-day-lag, forward-fill, no-backfill) date×series matrix. The window / mask /
standardizer helpers below are **stdlib-only** so the windowing/leakage-safety and
the fold-frozen standardizer math are unit-testable without torch installed.
"""
from __future__ import annotations

import base64
import math
from typing import Any, Iterable, Mapping, Sequence

from ..predictors.ssl_corpus_encoder import SSLCorpusEncoderPredictor
from .base import Trainer

TRAINER_QUALNAME = "ml.trainers.ssl_corpus_encoder.SSLCorpusEncoderTrainer"

_DEFAULTS: dict[str, Any] = {
    "seq_len": 64,          # L — trailing days per window
    "embedding_dim": 16,    # d — bottleneck width (the served embedding)
    "mask_ratio": 0.5,      # fraction of OBSERVED cells hidden for the MAE target
    "hidden": 64,           # trunk / decoder hidden width
    "epochs": 50,
    "batch_size": 64,
    "lr": 1.0e-3,
    "weight_decay": 0.0,
    "val_fraction": 0.2,    # trailing windows held out for the val recon loss
    "onnx_opset": 17,
    "parity_tol": 1.0e-4,
    "parity_ref_n": 256,
    "seed": 42,
    # Optional: pin the series (F axis) explicitly + in order. Omitted → ALL
    # series discovered across the panel rows, sorted deterministically.
    "series": None,
}

# The mask convention (single source of truth, mirrored in the predictor):
#   mask == 1.0  ⟺  the cell is ABSENT (native `None`/non-finite, OR MAE-hidden).
#   mask == 0.0  ⟺  the cell is OBSERVED and visible to the encoder.
_MISSING = 1.0
_PRESENT = 0.0


# --------------------------------------------------------------------------- #
# Pure, stdlib-only helpers (windowing / mask / fold-frozen standardizer).
# These are the tested contract — no torch/numpy needed.
# --------------------------------------------------------------------------- #
def resolve_series(
    rows: Sequence[Mapping[str, Any]], pinned: Sequence[str] | None = None
) -> list[str]:
    """The ordered series list (the ``F`` axis).

    ``pinned`` (config ``series``) fixes the set + order; otherwise the sorted
    union of every ``values`` key across the panel rows — deterministic so the
    window width and the served embedding are reproducible.
    """
    if pinned:
        series = [str(s) for s in pinned]
        if not series:
            raise ValueError("config 'series' must be non-empty when given")
        if len(set(series)) != len(series):
            raise ValueError(f"config 'series' has duplicates: {series}")
        return series
    seen: set[str] = set()
    for r in rows:
        vals = r.get("values") or {}
        seen.update(str(k) for k in vals)
    return sorted(seen)


def _cell(value: Any) -> float | None:
    """A finite float or ``None`` (a genuinely-missing cell — never a fake 0)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def build_panel_windows(
    rows: Sequence[Mapping[str, Any]],
    *,
    series: Sequence[str],
    seq_len: int,
) -> list[dict[str, Any]]:
    """Fixed-length ``L``-day windows over the (date-sorted) panel.

    Returns, for each window ending at date ``G`` (the ``seq_len``-th row onward),
    a dict ``{end_date, window_dates, values:(L×F), mask:(L×F)}`` where ``values``
    is the raw cell value (0.0 at a missing cell — a *placeholder*, the mask marks
    it) and ``mask`` is the missingness channel (``1.0`` ⟺ ``None``/non-finite).
    The first ``seq_len-1`` rows are dropped (incomplete window), exactly like the
    ``market_sequences`` causal windower. Leakage-safe: a window ending at ``G``
    contains only rows dated ``≤ G`` (the panel already enforces strict-prior).
    """
    if seq_len < 1:
        raise ValueError(f"seq_len must be >= 1; got {seq_len}")
    if not series:
        raise ValueError("series must be non-empty")

    ordered = sorted(rows, key=lambda r: str(r.get("date", "")))
    n_feat = len(series)
    dates: list[str] = []
    vecs: list[list[float]] = []
    masks: list[list[float]] = []
    for r in ordered:
        vals = r.get("values") or {}
        v = [0.0] * n_feat
        m = [_MISSING] * n_feat
        for f, series_id in enumerate(series):
            x = _cell(vals.get(series_id))
            if x is not None:
                v[f] = x
                m[f] = _PRESENT
        dates.append(str(r.get("date", "")))
        vecs.append(v)
        masks.append(m)

    out: list[dict[str, Any]] = []
    for i in range(seq_len - 1, len(ordered)):
        lo = i - seq_len + 1
        out.append(
            {
                "end_date": dates[i],
                "window_dates": dates[lo : i + 1],
                "values": [list(vecs[j]) for j in range(lo, i + 1)],
                "mask": [list(masks[j]) for j in range(lo, i + 1)],
            }
        )
    return out


def fit_cell_standardizer(
    windows_values: Sequence[Sequence[Sequence[float]]],
    windows_masks: Sequence[Sequence[Sequence[float]]],
    n_series: int,
) -> tuple[list[float], list[float]]:
    """Per-series (per-``F``) mean/std over the OBSERVED cells of the given windows.

    Population statistics (matching ``numpy``'s default ``std``), computed only
    over observed cells (``mask == 0``) — a natively-missing cell never enters the
    stats. A constant / all-missing series gets ``std`` guarded to ``1.0`` (mean
    ``0.0`` when never observed). Call this on the **train** windows only and
    freeze the result into ``model_state`` so serve-time uses the same stats
    (fold-freeze). Standardization itself is baked into the exported graph.
    """
    sums = [0.0] * n_series
    cnts = [0] * n_series
    for wv, wm in zip(windows_values, windows_masks):
        for row_v, row_m in zip(wv, wm):
            for f in range(n_series):
                if row_m[f] == _PRESENT:
                    sums[f] += row_v[f]
                    cnts[f] += 1
    mean = [(sums[f] / cnts[f]) if cnts[f] > 0 else 0.0 for f in range(n_series)]

    sq = [0.0] * n_series
    for wv, wm in zip(windows_values, windows_masks):
        for row_v, row_m in zip(wv, wm):
            for f in range(n_series):
                if row_m[f] == _PRESENT:
                    d = row_v[f] - mean[f]
                    sq[f] += d * d
    std: list[float] = []
    for f in range(n_series):
        s = math.sqrt(sq[f] / cnts[f]) if cnts[f] > 0 else 0.0
        std.append(s if s >= 1e-8 else 1.0)  # guard constant/absent channels
    return mean, std


def standardize_window(
    values: Sequence[Sequence[float]],
    mask: Sequence[Sequence[float]],
    mean: Sequence[float],
    std: Sequence[float],
) -> list[list[float]]:
    """Standardize observed cells; leave missing cells at ``0.0``.

    The exact serve math the exported encoder graph bakes in
    (``(value - mean) / std`` on observed cells, ``0.0`` on missing) — exposed as
    a pure function so the fold-frozen standardization is testable without torch.
    """
    n_feat = len(mean)
    out: list[list[float]] = []
    for row_v, row_m in zip(values, mask):
        out.append(
            [
                ((row_v[f] - mean[f]) / std[f]) if row_m[f] == _PRESENT else 0.0
                for f in range(n_feat)
            ]
        )
    return out


# --------------------------------------------------------------------------- #
# The trainer.
# --------------------------------------------------------------------------- #
class SSLCorpusEncoderTrainer(Trainer):
    """Masked-reconstruction encoder over the ``corpus_panel``; serves as ONNX."""

    PREDICTOR_CLASS = SSLCorpusEncoderPredictor

    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        import numpy as np  # noqa: PLC0415
        import torch  # noqa: PLC0415
        from torch import nn  # noqa: PLC0415

        cfg = {**_DEFAULTS, **dict(config)}
        rows = list(rows)
        seq_len = int(cfg["seq_len"])
        embedding_dim = int(cfg["embedding_dim"])
        mask_ratio = float(cfg["mask_ratio"])
        if not (0.0 < mask_ratio < 1.0):
            raise ValueError(f"mask_ratio must be in (0,1); got {mask_ratio}")

        series = resolve_series(rows, cfg.get("series"))
        n_feat = len(series)
        if n_feat < 1:
            raise ValueError("no series found in the panel rows")

        windows = build_panel_windows(rows, series=series, seq_len=seq_len)
        if not windows:
            raise ValueError(
                f"no usable windows: need >= {seq_len} panel rows (got {len(rows)})"
            )

        # (N, L, F) raw values + missingness mask (1 = absent).
        value = np.asarray([w["values"] for w in windows], dtype=np.float32)
        miss = np.asarray([w["mask"] for w in windows], dtype=np.float32)
        n = value.shape[0]

        # Time-ordered train/val split (trailing windows held out).
        val_fraction = float(cfg["val_fraction"])
        n_val = int(n * val_fraction)
        n_train = max(1, n - n_val)
        # Fold-frozen per-cell standardizer on the TRAIN windows only.
        mean_l, std_l = fit_cell_standardizer(
            [w["values"] for w in windows[:n_train]],
            [w["mask"] for w in windows[:n_train]],
            n_feat,
        )
        mean = np.asarray(mean_l, dtype=np.float32)
        std = np.asarray(std_l, dtype=np.float32)

        seed = int(cfg["seed"])
        torch.manual_seed(seed)
        np.random.seed(seed)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        hidden = int(cfg["hidden"])
        flat_dim = seq_len * n_feat

        # ---- encoder trunk (exported): raw value+mask (B,L,2F) → emb (B,d) ---
        # Standardization is baked in (first op) so the served graph consumes the
        # RAW value+mask serve input and parity covers normalization.
        class Encoder(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.register_buffer("mean", torch.tensor(mean, dtype=torch.float32))
                self.register_buffer("std", torch.tensor(std, dtype=torch.float32))
                self.fc1 = nn.Linear(2 * flat_dim, hidden)
                self.act = nn.ReLU()
                self.fc2 = nn.Linear(hidden, embedding_dim)

            def forward(self, x: "torch.Tensor") -> "torch.Tensor":
                # x: (B, L, 2F) — first F cols raw value, last F cols mask (1=hidden)
                v = x[..., :n_feat]
                m = x[..., n_feat:]
                v = (v - self.mean) / self.std
                v = v * (1.0 - m)  # zero hidden/missing cells → neutral (never signal)
                b = x.shape[0]
                flat = torch.cat([v.reshape(b, -1), m.reshape(b, -1)], dim=1)
                return self.fc2(self.act(self.fc1(flat)))

        # ---- decoder (train-only, discarded): emb (B,d) → recon (B,L,F) ------
        class Decoder(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.fc1 = nn.Linear(embedding_dim, hidden)
                self.act = nn.ReLU()
                self.fc2 = nn.Linear(hidden, flat_dim)

            def forward(self, emb: "torch.Tensor") -> "torch.Tensor":
                b = emb.shape[0]
                return self.fc2(self.act(self.fc1(emb))).reshape(b, seq_len, n_feat)

        encoder = Encoder().to(device)
        decoder = Decoder().to(device)
        mean_t = torch.from_numpy(mean).to(device)
        std_t = torch.from_numpy(std).to(device)
        opt = torch.optim.Adam(
            list(encoder.parameters()) + list(decoder.parameters()),
            lr=float(cfg["lr"]),
            weight_decay=float(cfg["weight_decay"]),
        )

        v_all = torch.from_numpy(value).to(device)
        miss_all = torch.from_numpy(miss).to(device)
        train_idx = np.arange(n_train)
        val_idx = np.arange(n_train, n)
        batch_size = int(cfg["batch_size"])
        epochs = int(cfg["epochs"])
        rng = np.random.default_rng(seed)

        def _recon_loss(
            idx: "np.ndarray", mae_rng: "np.random.Generator"
        ) -> "torch.Tensor":
            v = v_all[idx]                     # (b, L, F) raw
            native = miss_all[idx]            # (b, L, F) 1 = natively missing
            observed = 1.0 - native
            # Artificially hide `mask_ratio` of the OBSERVED cells (the MAE target).
            r = torch.from_numpy(
                mae_rng.random(size=tuple(v.shape)).astype("float32")
            ).to(device)
            mae = ((r < mask_ratio).float() * observed)  # 1 = observed-but-hidden
            input_mask = torch.clamp(native + mae, max=1.0)  # hidden from the encoder
            x = torch.cat([v, input_mask], dim=-1)  # (b, L, 2F)
            recon = decoder(encoder(x))
            target = (v - mean_t) / std_t          # standardized truth
            denom = mae.sum().clamp(min=1.0)
            return ((recon - target) ** 2 * mae).sum() / denom

        last_train_loss = 0.0
        encoder.train()
        decoder.train()
        for _epoch in range(epochs):
            perm = rng.permutation(train_idx)
            epoch_loss = 0.0
            n_seen = 0
            for start in range(0, len(perm), batch_size):
                bidx = perm[start : start + batch_size]
                opt.zero_grad()
                loss = _recon_loss(bidx, rng)
                loss.backward()
                opt.step()
                epoch_loss += float(loss.detach().cpu()) * len(bidx)
                n_seen += len(bidx)
            last_train_loss = epoch_loss / max(1, n_seen)

        # Held-out reconstruction loss (deterministic MAE mask via a fixed seed).
        encoder.eval()
        decoder.eval()
        with torch.no_grad():
            if len(val_idx):
                val_loss = float(
                    _recon_loss(val_idx, np.random.default_rng(seed + 1)).cpu()
                )
            else:
                val_loss = float(last_train_loss)

        # ---- export the ENCODER only + fail-closed CPU-ONNX parity gate ------
        from .onnx_export import export_and_verify  # noqa: PLC0415

        served = encoder.to("cpu").eval()
        ref_n = min(int(cfg["parity_ref_n"]), n)
        # Serve input = raw value + NATIVE missingness mask (no MAE at serve).
        serve_x = np.concatenate([value[:ref_n], miss[:ref_n]], axis=-1).astype(
            np.float32
        )
        ref = torch.from_numpy(serve_x).to("cpu")
        onnx_bytes, parity = export_and_verify(
            served, ref, tol=float(cfg["parity_tol"]), opset=int(cfg["onnx_opset"])
        )

        return {
            "trainer": TRAINER_QUALNAME,
            "arch": "mae_mlp",
            "series": list(series),
            "seq_len": seq_len,
            "embedding_dim": embedding_dim,
            "mask_ratio": mask_ratio,
            "onnx_b64": base64.b64encode(onnx_bytes).decode("ascii"),
            "standardizer": {"mean": mean_l, "std": std_l},
            "input_layout": "concat_value_mask_last_axis",  # (B, L, [value(F) | mask(F)])
            "params": {
                "hidden": hidden,
                "epochs": epochs,
                "batch_size": batch_size,
                "lr": float(cfg["lr"]),
                "weight_decay": float(cfg["weight_decay"]),
                "val_fraction": val_fraction,
                "seed": seed,
            },
            # Metrics (fit() return contract — mirrors the TCN trainer's inline shape).
            "n_windows": int(n),
            "n_train_windows": int(n_train),
            "n_series": int(n_feat),
            "reconstruction_loss": float(last_train_loss),
            "val_loss": float(val_loss),
            "parity": parity,
            "parity_max_abs_diff": float(parity.get("max_abs_diff", 0.0)),
        }
