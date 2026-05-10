"""`ShadowPredictor` — non-influencing model-call wrapper (S-AI-WS7-PART-1).

Composes any `Predictor` with a structured audit logger. Every
`predict(row)` call emits one JSONL line carrying
`(predicted_at_utc, model_id, stage, score, row_keys)` and
returns the wrapped predictor's score unchanged.

The wrapper does NOT decide what the caller does with the score —
it is a pure side-channel observer. In shadow mode the caller is
expected to discard the score; in advisory mode the caller can
display or veto on it; in higher tiers the caller can use it
directly. Wiring into the live trading pipeline is intentionally
out of scope for this sprint (S-AI-WS7-PART-1); see the sprint
log's "Out of scope" section.

Audit log format (one JSON object per line, UTF-8):

    {
      "predicted_at_utc": "2026-05-10T17:00:00+00:00",
      "model_id":  "setup-quality-baseline-v0",
      "stage":     "shadow",
      "score":     2.713,
      "row_keys":  ["strategy_name", "setup_type", "killzone", ...]
    }

`row_keys` is the sorted list of input feature names, NOT the
values — protects against accidental capture of operator-side
state in the audit trail. Operators inspect the score; if they
need full row context they can replay the pipeline from
`signal_audit.jsonl`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..manifest import VALID_DEPLOYMENT_STAGES
from .base import Predictor


class ShadowPredictor(Predictor):
    def __init__(
        self,
        wrapped: Predictor,
        *,
        model_id: str,
        stage: str,
        log_path: Path | None = None,
    ) -> None:
        if not isinstance(wrapped, Predictor):
            raise TypeError(
                f"wrapped must be a Predictor; got {type(wrapped).__name__}"
            )
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id must be a non-empty string")
        if stage not in VALID_DEPLOYMENT_STAGES:
            raise ValueError(
                f"stage must be one of {VALID_DEPLOYMENT_STAGES}; got {stage!r}"
            )
        self._wrapped = wrapped
        self._model_id = model_id
        self._stage = stage
        self._log_path: Path | None = (
            Path(log_path) if log_path is not None else None
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def stage(self) -> str:
        return self._stage

    def predict(self, row: Mapping[str, Any]) -> float:
        score = float(self._wrapped.predict(row))
        if self._log_path is not None:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "predicted_at_utc": datetime.now(timezone.utc).isoformat(),
                "model_id": self._model_id,
                "stage": self._stage,
                "score": score,
                "row_keys": sorted(row.keys()),
            }
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload) + "\n")
        return score
