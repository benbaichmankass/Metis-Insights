"""`ShadowPredictor` — non-influencing model-call wrapper (S-AI-WS7-PART-1).

Composes any `Predictor` with a structured audit logger. Every
`predict(row)` call emits one JSONL line carrying
`(predicted_at_utc, model_id, stage, score, row_keys, feature_row)`
and returns the wrapped predictor's score unchanged.

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
      "row_keys":  ["strategy_name", "setup_type", "killzone", ...],
      "feature_row": {
        "strategy_name": "vwap",
        "symbol": "BTCUSDT",
        "direction": "buy",
        "confidence": 0.65,
        "setup_type": "vwap_revert",
        "killzone": "ny",
        "bias": "long"
      }
    }

`row_keys` is the sorted list of input feature names; `feature_row`
is the full values dict captured from the strategy's signal-time
projection (added 2026-05-19 so every shadow record carries the
strategy+symbol context needed for a deterministic trade↔score
join). Both fields are JSON-safe: non-serializable values in
`feature_row` are coerced to `str(value)` rather than dropping the
record.

The `feature_row` content is operationally relevant signal-time
metadata (strategy name, symbol, direction, etc.). It does NOT
contain operator-side secrets or risk-cap state — the strategy's
`_build_shadow_feature_row` helpers explicitly project only the
signal-time surface.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..manifest import canonical_stage
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
        # Normalize the stage through the alias map (accept a legacy 7-stage
        # name, store/log the canonical one). Raises ValueError on garbage.
        stage = canonical_stage(stage)
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

    @property
    def wrapped(self) -> Predictor:
        """The composed base predictor.

        Lets a caller introspect model-specific metadata (e.g. a regime
        model's `regime_spec`) without bypassing the audit-log surface
        — `predict()` is still the only way to score.
        """
        return self._wrapped

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
                "feature_row": _coerce_json_safe(row),
            }
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload) + "\n")
        return score


def _coerce_json_safe(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project ``row`` into a JSON-serializable dict.

    Built-in JSON types (str / int / float / bool / None) pass
    through unchanged. Anything else is coerced via ``str(value)``
    so a single odd value can't break the audit-log write — losing
    the structured form is acceptable for a side-channel; losing
    the whole record is not.
    """
    out: dict[str, Any] = {}
    for k, v in row.items():
        key = str(k)
        if v is None or isinstance(v, (bool, int, float, str)):
            out[key] = v
        else:
            out[key] = str(v)
    return out
