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
from ml.calibration.regime_alignment import (
    load_regime_alignment,
    predict_alignment,
)

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
    direction: str | None = None,
    regime_alignment: dict[str, dict[str, Calibrator]] | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Return ``(inputs, provenance)`` for ``compute_conviction``.

    * ``inputs`` — ``{c_strat, c_setup?, c_wr?, c_reg?}`` calibrated to [0,1].
    * ``provenance`` — per-slot contributing model_ids + stages (for the
      observe-only log; lets a later review see what fed the score).

    ``c_reg`` flows **only** when a ``regime_alignment`` calibrator exists for
    the regime head's ``model_id`` — it maps the head's logged score + the trade
    ``direction`` to ``P(favorable | regime, direction)`` (see
    ``ml.calibration.regime_alignment``). When NO such calibrator is present the
    regime head falls through to ``_default_normalize`` which returns ``None`` →
    ``c_reg`` is dropped, **byte-for-byte the pre-calibrator behaviour**
    (conviction renormalizes over the remaining inputs). ``direction`` /
    ``regime_alignment`` are optional so existing callers are unaffected.

    Never raises; on any per-input error that input is dropped.
    """
    cal = calibrators or {}
    ra = regime_alignment or {}
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
        # c_reg gets its OWN calibrator family: a regime-alignment calibrator
        # maps (regime score, direction) -> P(favorable | regime, direction).
        # Present → c_reg flows; absent → _default_normalize returns None and
        # c_reg is dropped, exactly as before any regime calibrator shipped.
        if slot == "c_reg":
            model_cals = ra.get(model_id)
            if model_cals:
                aligned = predict_alignment(model_cals, score, direction)
                if aligned is not None:
                    acc.setdefault(slot, []).append(_clip01(aligned))
                    acc_prov.setdefault(slot, []).append(
                        {"model_id": model_id, "stage": stage,
                         "calibrated": True, "kind": "regime_alignment",
                         "direction": direction, "raw": score}
                    )
                    continue
                # alignment calibrator present but yielded nothing (bad score) →
                # fall through to the default path (which drops c_reg).
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
# Separate cache for the regime_alignment section of the SAME artifact.
_ra_cache: dict[str, dict[str, Calibrator]] | None = None
_ra_mtime: float | None = None


def _mirrored_cal_path() -> str:
    """The trainer-mirror calibrators path on the live VM.

    Trainer-fit calibrators ride the trainer mirror (like the registry / sweeps)
    and land under ``runtime_logs/trainer_mirror/calibration/calibrators.json``
    on the live VM (published by ``scripts/ops/publish_trainer_mirror.sh``).
    Resolved through the same ``runtime_logs_dir()`` helper the rest of the live
    path uses, so it tracks ``DATA_DIR``. Best-effort: any import/resolution
    error yields an empty string (treated as "not present" by the caller).
    """
    try:
        from src.utils.paths import runtime_logs_dir

        return str(runtime_logs_dir() / "trainer_mirror" / "calibration"
                   / "calibrators.json")
    except Exception:
        return ""


def _cal_path() -> str:
    """Resolve the calibrators artifact path, first existing file wins.

    Order (fail-permissive — none existing → the default is returned and the
    caller falls back to raw normalization):
      1. ``CONVICTION_CALIBRATORS_PATH`` env override (always honored if set,
         even when the file doesn't exist yet, so an operator pin is respected).
      2. The mirrored live path (trainer-fit calibrators, the production source).
      3. The legacy in-repo default ``artifacts/calibration/calibrators.json``.
    """
    env = os.environ.get("CONVICTION_CALIBRATORS_PATH")
    if env:
        return env
    mirrored = _mirrored_cal_path()
    if mirrored and os.path.exists(mirrored):
        return mirrored
    return _DEFAULT_CAL_PATH


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


def load_regime_alignment_cached() -> dict[str, dict[str, Calibrator]]:
    """Process-cached load of the artifact's ``regime_alignment`` section.

    Returns ``{model_id: {direction: Calibrator}}``, refreshed on artifact mtime
    change. Fail-permissive: ``{}`` when the artifact is missing / the section is
    absent — so ``c_reg`` simply stays dropped (byte-for-byte the pre-calibrator
    behaviour). Same path-resolution + cache discipline as
    ``load_calibrators_cached``; reads the same file (one artifact, two
    sections).
    """
    global _ra_cache, _ra_mtime
    path = _cal_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {} if _ra_cache is None else _ra_cache
    with _cal_lock:
        if _ra_cache is None or mtime != _ra_mtime:
            try:
                raw = json.loads(Path(path).read_text())
            except (OSError, ValueError):
                raw = None
            _ra_cache = load_regime_alignment(raw)
            _ra_mtime = mtime
        return _ra_cache
