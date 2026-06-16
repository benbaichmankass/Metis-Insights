"""Adapter: per-model shadow/advisory scores + strategy confidence -> calibrated
conviction inputs (design doc § 3, § 4a).

Bridges the existing `model_scores` capture (`{model_id: {stage, score}}`) and
the strategy's own signal confidence into the ``[0,1]`` P(win) inputs the
conviction blend (`src/runtime/conviction.py`) consumes.

**Per-model stage is respected** (operator clarification 2026-06-16): the
per-model 7-stage ladder is unchanged; each model's *stage* decides which
conviction it feeds — `shadow` → the observed conviction (no influence),
`advisory`+ → also the influencing conviction. Pass ``influencing_only=True``
to build the advisory+-only view.

Calibrators (fit offline by ``scripts/ml/fit_confidence_calibrators.py``) are
loaded read-only from a JSON artifact; absent → raw scores pass through a sane
default normalization. Pure / stdlib + the pure-Python ``Calibrator`` (no
sklearn on the live path). Every entry point is fail-permissive.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ml.calibration.calibrators import Calibrator

# Stages whose models may influence the order path (mirror of the existing
# advisory-stage set; the per-model ladder itself is unchanged).
INFLUENCE_STAGES = frozenset({"advisory", "limited_live", "live_approved"})

# Default calibrators artifact (written by fit_confidence_calibrators.py).
_DEFAULT_CAL_PATH = "artifacts/calibration/calibrators.json"


def classify_head(model_id: str) -> str | None:
    """Map a model_id to its conviction-lens input slot, or None if it is not a
    *conviction* input (execution-quality / prop-mission feed the sizing lens;
    unknown heads are ignored)."""
    m = (model_id or "").lower()
    if "execution-quality" in m or "prop-mission" in m:
        return None  # sizing/feasibility lens, not conviction
    if "trade-outcome" in m:
        return "c_wr"
    if "setup-quality" in m:
        return "c_setup"
    if "regime" in m:
        return "c_reg"
    return None


def _clip01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _default_normalize(slot: str, score: float) -> float | None:
    """Map a raw head score to [0,1] P(win) basis when no calibrator exists.

    Returns None to *skip* an input we can't honestly normalize yet.
    """
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if slot == "c_wr":
        # trade-outcome heads already emit P(win) in [0,1]
        return _clip01(s)
    if slot == "c_setup":
        # setup-quality is an R-multiple ~[-3,3]; map to [0,1]
        s = max(-3.0, min(3.0, s))
        return _clip01((s + 3.0) / 6.0)
    # c_reg: a single stored scalar is not a usable regime-alignment probability
    # without the class vector — skip in v1 unless a head calibrator is provided.
    return None


def build_conviction_inputs(
    strategy_name: str,
    raw_confidence: float | None,
    model_scores: dict[str, dict] | None,
    *,
    calibrators: dict[str, Calibrator] | None = None,
    influencing_only: bool = False,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Return ``(inputs, provenance)`` for ``compute_conviction``.

    * ``inputs`` — ``{c_strat, c_setup?, c_wr?, c_reg?}`` calibrated to [0,1].
    * ``provenance`` — per-slot contributing model_ids + stages (for the
      observe-only log; lets a later review see what fed the score).

    Never raises; on any per-input error that input is dropped.
    """
    cal = calibrators or {}
    inputs: dict[str, float] = {}
    prov: dict[str, Any] = {}

    # c_strat: the strategy's own (calibrated) signal confidence — always "live".
    if raw_confidence is not None:
        try:
            rc = float(raw_confidence)
            c = cal.get(strategy_name)
            inputs["c_strat"] = c.predict(rc) if c is not None else _clip01(rc)
            prov["c_strat"] = {"source": strategy_name,
                               "calibrated": c is not None, "raw": rc}
        except (TypeError, ValueError):
            pass

    # head inputs: average per slot, respecting per-model stage
    acc: dict[str, list[float]] = {}
    acc_prov: dict[str, list[dict]] = {}
    for model_id, rec in (model_scores or {}).items():
        if not isinstance(rec, dict):
            continue
        slot = classify_head(model_id)
        if slot is None:
            continue
        stage = rec.get("stage")
        if influencing_only and stage not in INFLUENCE_STAGES:
            continue
        c = cal.get(model_id)
        score = rec.get("score")
        if c is not None:
            try:
                val: float | None = c.predict(float(score))
            except (TypeError, ValueError):
                val = None
        else:
            val = _default_normalize(slot, score)
        if val is None:
            continue
        acc.setdefault(slot, []).append(_clip01(val))
        acc_prov.setdefault(slot, []).append(
            {"model_id": model_id, "stage": stage,
             "calibrated": c is not None, "raw": score}
        )

    for slot, vals in acc.items():
        inputs[slot] = sum(vals) / len(vals)
        prov[slot] = acc_prov[slot]

    return inputs, prov


# --------------------------------------------------------------------------- #
# read-only calibrator artifact loader (cached; live-path safe)
# --------------------------------------------------------------------------- #
_cal_lock = threading.Lock()
_cal_cache: dict[str, Calibrator] | None = None
_cal_mtime: float | None = None


def _cal_path() -> str:
    return os.environ.get("CONVICTION_CALIBRATORS_PATH") or _DEFAULT_CAL_PATH


def load_calibrators(path: str) -> dict[str, Calibrator]:
    """Load ``{name: Calibrator}`` from a JSON artifact. Empty on absence/error."""
    out: dict[str, Calibrator] = {}
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return out
    for name, d in (raw or {}).items():
        try:
            out[name] = Calibrator.from_dict(d)
        except (ValueError, KeyError, TypeError):
            continue
    return out


def load_calibrators_cached() -> dict[str, Calibrator]:
    """Process-cached calibrator load, refreshed on artifact mtime change.

    Fail-permissive: returns ``{}`` (identity normalization) if the artifact is
    missing — so the observe-only conviction still computes from raw confidence.
    """
    global _cal_cache, _cal_mtime
    path = _cal_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {} if _cal_cache is None else _cal_cache
    with _cal_lock:
        if _cal_cache is None or mtime != _cal_mtime:
            _cal_cache = load_calibrators(path)
            _cal_mtime = mtime
        return _cal_cache
