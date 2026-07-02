"""Torch sequence-model trainer — small causal TCN (M19 T1.1).

The first **deep** trainer: a small causal Temporal Convolutional Network over the
per-row ``seq_window`` (shape ``(seq_len, n_features)``) materialized by
:mod:`ml.datasets.sequence_window`. It exists to answer one falsifiable question —
*does a deep sequence model over raw bars beat the class-weighted LightGBM regime
head on the same purged-CV gate?* — under the M19 Tier-1 GPU-burst tier.

Design contract (why it's shaped this way):

- **GPU-train, CPU-serve.** ``fit`` trains on CUDA when available (else CPU;
  identical math, CUDA only faster) and returns a ``model_state`` whose served
  artifact is an **ONNX graph** (base64), not a torch object — so the existing
  JSON registry / mirror / bundle plumbing carries it unchanged, and the predictor
  serves it via ``onnxruntime`` on the CPU money-box. **No torch on the live box.**
- **Fail-closed parity.** Before returning, the trained module is ONNX-exported
  and its CPU-ONNX output is verified against the torch output
  (:func:`ml.trainers.onnx_export.export_and_verify`). A parity miss raises — a
  model that can't be reproduced on the serve target is never emitted.
- **Standardization baked into the graph.** Per-channel mean/std (frozen from the
  training windows) is the module's first layer, so the served ONNX consumes the
  RAW ``seq_window`` and parity covers normalization too — the predictor does no
  arithmetic of its own.
- **Torch is lazy.** ``torch`` / ``numpy`` are imported inside ``fit`` and the TCN
  class is defined there, so this module imports on a torch-free environment
  (paired predictor + the money-box). Same discipline as the LightGBM trainer.
"""
from __future__ import annotations

import base64
from typing import Any, Iterable, Mapping

from ..datasets.sequence_window import SEQ_WINDOW_COLUMN
from ..predictors.torch_sequence import TorchSequencePredictor
from .base import Trainer

# Forbidden as features when the target is `regime_label` (mirrors the LightGBM
# trainer). Forward-window columns are derived from the label and would leak it.
_REGIME_FORBIDDEN: frozenset[str] = frozenset(
    {"regime_label", "forward_log_return", "forward_log_return_vol"}
)

_DEFAULTS: dict[str, Any] = {
    "seq_len": 64,
    "channels": 48,
    "kernel_size": 2,
    "dilations": [1, 2, 4, 8, 16],
    "epochs": 30,
    "batch_size": 256,
    "lr": 1.0e-3,
    "weight_decay": 0.0,
    "onnx_opset": 17,
    "parity_tol": 1.0e-4,
    "parity_ref_n": 256,
    "seed": 42,
}


def _check_leakage(feature_columns: list[str], target: str) -> None:
    if target in feature_columns:
        raise ValueError(f"feature_columns contains the target {target!r}; leakage")
    bad = sorted(set(feature_columns) & _REGIME_FORBIDDEN)
    if bad:
        raise ValueError(f"feature_columns contains forbidden {bad} against {target!r}")


class TorchSequenceTrainer(Trainer):
    """Small causal TCN over ``seq_window``; serves as ONNX on CPU."""

    PREDICTOR_CLASS = TorchSequencePredictor

    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        import numpy as np  # noqa: PLC0415
        import torch  # noqa: PLC0415
        from torch import nn  # noqa: PLC0415

        cfg = {**_DEFAULTS, **dict(config)}
        target = str(cfg.get("target_column", "regime_label"))
        feature_columns = [str(c) for c in cfg.get("feature_columns", [])]
        if not feature_columns:
            raise ValueError("trainer_config.feature_columns must be non-empty")
        _check_leakage(feature_columns, target)
        seq_len = int(cfg["seq_len"])
        n_feat = len(feature_columns)
        window_column = str(cfg.get("window_column", SEQ_WINDOW_COLUMN))

        # ---- collect windows + labels -------------------------------------
        windows: list[list[list[float]]] = []
        labels: list[str] = []
        spec_symbol = ""
        spec_timeframe = ""
        for row in rows:
            label_raw = row.get(target)
            win = row.get(window_column)
            if label_raw is None or win is None:
                continue
            label = str(label_raw).strip()
            if not label:
                continue
            if not isinstance(win, list) or len(win) != seq_len:
                # A malformed / wrong-length window is a data-contract violation
                # for this row — skip it rather than train on a ragged tensor.
                continue
            windows.append([[float(v) for v in bar] for bar in win])
            labels.append(label)
            if not spec_symbol:
                spec_symbol = str(row.get("symbol") or "")
            if not spec_timeframe:
                spec_timeframe = str(row.get("timeframe") or "")

        if not windows:
            raise ValueError(
                f"no usable rows: need '{window_column}' (len {seq_len}) + '{target}' on each"
            )
        class_labels = sorted(set(labels))
        if len(class_labels) < 2:
            raise ValueError(f"need >= 2 classes; got {class_labels}")
        label_to_idx = {c: i for i, c in enumerate(class_labels)}
        n_classes = len(class_labels)

        x = np.asarray(windows, dtype=np.float32)  # (N, L, F)
        y = np.asarray([label_to_idx[lbl] for lbl in labels], dtype=np.int64)
        if x.shape[1:] != (seq_len, n_feat):
            raise ValueError(
                f"window shape {x.shape[1:]} != (seq_len={seq_len}, n_feat={n_feat}); "
                "feature_columns must match the materialized window width"
            )

        # ---- per-channel standardizer (frozen from training windows) ------
        mean = x.reshape(-1, n_feat).mean(axis=0)
        std = x.reshape(-1, n_feat).std(axis=0)
        std = np.where(std < 1e-8, 1.0, std)  # guard constant channels

        # ---- class weights (inverse-base-rate, from manifest or auto) -----
        class_weight_cfg = cfg.get("class_weight")
        if class_weight_cfg is not None:
            if not isinstance(class_weight_cfg, dict):
                raise ValueError("class_weight must be a dict {label: float}")
            missing = sorted(set(class_labels) - set(map(str, class_weight_cfg)))
            if missing:
                raise ValueError(f"class_weight missing entries for {missing}")
            weights = np.asarray(
                [float(class_weight_cfg[c]) for c in class_labels], dtype=np.float32
            )
        else:
            counts = np.bincount(y, minlength=n_classes).astype(np.float64)
            counts = np.where(counts == 0, 1.0, counts)
            w = counts.sum() / (n_classes * counts)
            weights = w.astype(np.float32)

        seed = int(cfg["seed"])
        torch.manual_seed(seed)
        np.random.seed(seed)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # ---- model: causal TCN → global-avg-pool → linear (logits) --------
        def _causal_pad(t: "torch.Tensor", pad_left: int) -> "torch.Tensor":
            return nn.functional.pad(t, (pad_left, 0))

        class TCNClassifier(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.register_buffer("mean", torch.tensor(mean, dtype=torch.float32))
                self.register_buffer("std", torch.tensor(std, dtype=torch.float32))
                ch = int(cfg["channels"])
                ks = int(cfg["kernel_size"])
                self._pads: list[int] = []
                convs = []
                in_ch = n_feat
                for d in list(cfg["dilations"]):
                    convs.append(nn.Conv1d(in_ch, ch, ks, dilation=int(d)))
                    self._pads.append(int(d) * (ks - 1))
                    in_ch = ch
                self.convs = nn.ModuleList(convs)
                self.act = nn.ReLU()
                self.head = nn.Linear(ch, n_classes)

            def forward(self, x: "torch.Tensor") -> "torch.Tensor":
                # x: (batch, L, F) raw → standardize → (batch, F, L) for conv1d.
                x = (x - self.mean) / self.std
                h = x.transpose(1, 2)
                for conv, pad in zip(self.convs, self._pads):
                    h = self.act(conv(_causal_pad(h, pad)))
                h = h.mean(dim=2)  # global average pool over time
                return self.head(h)  # logits (batch, n_classes)

        class ProbaModule(nn.Module):
            """Serving wrapper — softmax(logits). This is what we export."""

            def __init__(self, base: nn.Module) -> None:
                super().__init__()
                self.base = base
                self.softmax = nn.Softmax(dim=1)

            def forward(self, x: "torch.Tensor") -> "torch.Tensor":
                return self.softmax(self.base(x))

        base = TCNClassifier().to(device)
        x_t = torch.from_numpy(x).to(device)
        y_t = torch.from_numpy(y).to(device)
        w_t = torch.from_numpy(weights).to(device)
        loss_fn = nn.CrossEntropyLoss(weight=w_t)
        opt = torch.optim.Adam(
            base.parameters(), lr=float(cfg["lr"]), weight_decay=float(cfg["weight_decay"])
        )

        n = x_t.shape[0]
        batch_size = int(cfg["batch_size"])
        epochs = int(cfg["epochs"])
        base.train()
        rng = np.random.default_rng(seed)
        last_loss = 0.0
        for _epoch in range(epochs):
            perm = rng.permutation(n)
            epoch_loss = 0.0
            for start in range(0, n, batch_size):
                idx = perm[start : start + batch_size]
                bx = x_t[idx]
                by = y_t[idx]
                opt.zero_grad()
                logits = base(bx)
                loss = loss_fn(logits, by)
                loss.backward()
                opt.step()
                epoch_loss += float(loss.detach().cpu()) * len(idx)
            last_loss = epoch_loss / max(1, n)

        # ---- export + fail-closed CPU-ONNX parity gate --------------------
        from .onnx_export import export_and_verify  # noqa: PLC0415

        served = ProbaModule(base).to("cpu").eval()
        ref_n = min(int(cfg["parity_ref_n"]), n)
        ref = torch.from_numpy(x[:ref_n]).to("cpu")
        onnx_bytes, parity = export_and_verify(
            served, ref, tol=float(cfg["parity_tol"]), opset=int(cfg["onnx_opset"])
        )

        return {
            "trainer": "ml.trainers.torch_sequence.TorchSequenceTrainer",
            "target_column": target,
            "feature_columns": list(feature_columns),
            "seq_len": seq_len,
            "window_column": window_column,
            "class_labels": list(class_labels),
            "onnx_b64": base64.b64encode(onnx_bytes).decode("ascii"),
            "standardizer": {"mean": mean.tolist(), "std": std.tolist()},
            "class_weight": {c: float(weights[i]) for i, c in enumerate(class_labels)},
            "arch": "tcn",
            "params": {
                "channels": int(cfg["channels"]),
                "kernel_size": int(cfg["kernel_size"]),
                "dilations": list(cfg["dilations"]),
                "epochs": epochs,
                "batch_size": batch_size,
                "lr": float(cfg["lr"]),
                "weight_decay": float(cfg["weight_decay"]),
                "seed": seed,
            },
            "n_train": int(n),
            "n_classes": n_classes,
            "final_train_loss": float(last_loss),
            "parity": parity,
            "symbol": spec_symbol,
            "timeframe": spec_timeframe,
        }
