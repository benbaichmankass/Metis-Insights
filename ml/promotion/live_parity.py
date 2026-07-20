"""Live serving-mechanics parity evidence (M25 gate reframe, 2026-07-19).

Operator-approved policy (docs/research/M25-promotion-consolidation-DESIGN.md
§ "The promotion gate — REFRAMED 2026-07-19"): an ML head's EDGE is proven
OFFLINE — the powered purged-walk-forward ``oos_edge`` gate — and the live
shadow soak's job is to prove serving MECHANICS: that the live pipeline feeds
the model the features it trained on, and that the logged score is the score
the registered artifact actually produces. This module computes that evidence
from EXISTING artifacts only (``runtime_logs/shadow_predictions.jsonl`` rows
carrying ``feature_row`` + the registered model artifact + the training
dataset) — no new live instrumentation.

Two result objects feed two gates in ``ml.promotion.gates``:

- :class:`LiveParityResult` (→ ``live_parity`` gate) — v1 scope:

  a. **Serving fidelity** — re-score up to ``sample_n`` most-recent live
     rows with the registered artifact; the logged score must match the
     recomputed score within ``score_tol``. A row whose re-score *raises*
     counts as a mismatch (the artifact cannot reproduce the logged score —
     that IS a serving-fidelity failure, not an evidence gap).
  b. **Dead-feature parity** (the ETH-xa bug class,
     ``BL-20260628-XA-TRAINING-ZERO``) — for each feature the model consumes,
     compare live rows vs the training dataset: a feature that is
     constant/all-zeros on ONE side but varying on the other is dead on the
     constant side and fails the gate, named in the detail.

- :class:`LabelsAccruingResult` (→ ``labels_accruing`` gate) — the labeled
  fraction of the head's live rows, catching the stale-candle-base labeling
  blockage class (e.g. MES 1213/1861 unlabeled, ``BL-20260626-MES-BASE-STALE``).

Fail-safe direction: an ERROR while computing (unreadable log, model-load
failure, missing dataset) surfaces as a populated ``error`` field, which the
gate maps to ``insufficient_data`` — never a silent pass, never a crash of
the whole gate-check.

Pure decision-support: reads the shadow log, the registry, and a dataset off
disk. It never registers a model, never writes a log, and never touches the
order path.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# Default knobs (mirrored as GateThresholds fields so profiles can tune them).
DEFAULT_SAMPLE_N = 50
DEFAULT_SCORE_TOL = 1e-6
DEFAULT_MAX_TRAIN_ROWS = 5000
# Dead-feature liveness window (rows). Must span MORE than one calendar day at
# the head's bar cadence, or calendar features are structurally "dead":
# ``dayofweek`` is constant within any single UTC day, so judging liveness over
# the 50-row fidelity sample (~12.5h at 15m) flags it on EVERY sub-24h window —
# the 2026-07-20 BTC/SOL false blocker. 672 rows = 7 days at 15m / ~2.3 days at
# 5m — every calendar feature sees multiple distinct values, while a genuinely
# dead pipeline feature (the ETH-xa class, zero for weeks) still reads dead.
DEFAULT_DEAD_WINDOW_N = 672
# Serving-fidelity rows must postdate the CURRENT artifact's training run by
# this grace (mirror-sync + predictor-reload lag) before they count. Without
# this scoping, a nightly-retrained head fails fidelity 100% STRUCTURALLY —
# the check re-scores yesterday's rows (scored by yesterday's artifact) with
# today's artifact (the 2026-07-20 BTC/SOL "mismatch 50/50" false blocker;
# shadow rows carry no artifact identity to key on, so time is the proxy).
DEFAULT_ARTIFACT_GRACE_S = 1800.0


@dataclass(frozen=True)
class LiveParityResult:
    """Computed serving-mechanics parity evidence for one model."""

    model_id: str
    n_live_rows: int = 0        # live (non-backfill) rows carrying feature_row
    n_sampled: int = 0          # rows actually re-scored
    n_mismatched: int = 0       # |logged − recomputed| > tol (incl. re-score errors)
    score_tol: float = DEFAULT_SCORE_TOL
    dead_live_features: tuple[str, ...] = field(default_factory=tuple)
    dead_train_features: tuple[str, ...] = field(default_factory=tuple)
    train_available: bool = False  # training dataset was readable for (b)
    error: str | None = None
    # Artifact-freshness scoping (2026-07-20): when the entry's newest
    # training run is resolvable, `artifact_at` is its ISO timestamp and
    # `n_fresh_rows` counts live rows logged AFTER it (+ grace) — the only
    # rows the CURRENT artifact can be expected to reproduce. `None` on a
    # legacy entry with no run history (fidelity then samples all rows,
    # the pre-scoping behaviour).
    artifact_at: str | None = None
    n_fresh_rows: int | None = None

    @property
    def mismatch_fraction(self) -> float | None:
        if self.n_sampled <= 0:
            return None
        return self.n_mismatched / self.n_sampled

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "n_live_rows": self.n_live_rows,
            "n_sampled": self.n_sampled,
            "n_mismatched": self.n_mismatched,
            "mismatch_fraction": self.mismatch_fraction,
            "score_tol": self.score_tol,
            "dead_live_features": list(self.dead_live_features),
            "dead_train_features": list(self.dead_train_features),
            "train_available": self.train_available,
            "error": self.error,
            "artifact_at": self.artifact_at,
            "n_fresh_rows": self.n_fresh_rows,
        }


@dataclass(frozen=True)
class LabelsAccruingResult:
    """Labeled fraction of a head's live rows (labeling-pipeline health)."""

    model_id: str
    n_live_rows: int = 0
    n_labeled: int = 0
    error: str | None = None

    @property
    def labeled_fraction(self) -> float | None:
        if self.n_live_rows <= 0:
            return None
        return self.n_labeled / self.n_live_rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "n_live_rows": self.n_live_rows,
            "n_labeled": self.n_labeled,
            "labeled_fraction": self.labeled_fraction,
            "error": self.error,
        }


def labels_accruing_from_counts(
    model_id: str, *, n_live_rows: int, n_unlabeled: int,
) -> LabelsAccruingResult:
    """Build a :class:`LabelsAccruingResult` from RG4-replay counts.

    The RG4 Stage-2 replay (``scripts/ml/replay_pregate_live.run``) already
    walks every live record for the head and reports ``n_records`` /
    ``n_unlabeled`` (rows whose realized-label candle join failed). The
    labeled count is approximated as ``n_records − n_unlabeled`` — rows with
    an unparseable timestamp or a re-score failure are counted as labeled by
    this approximation, which slightly *overstates* the fraction; the gate is
    therefore conservative in the safe direction only for genuinely-blocked
    labeling (the failure class it exists to catch).
    """
    n_rows = max(0, int(n_live_rows))
    labeled = max(0, n_rows - max(0, int(n_unlabeled)))
    return LabelsAccruingResult(
        model_id=model_id, n_live_rows=n_rows, n_labeled=labeled,
    )


def _num(v: Any) -> Any:
    """Numeric coercion mirroring ``scripts/ml/replay_pregate_live._num`` —
    the same coercion the RG4 replay applies before ``predict``-ing a logged
    row, so fidelity is measured on the row the artifact would actually see."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return v  # leave categoricals (e.g. vol_bucket) as-is for the encoder


def _norm_value(v: Any) -> Any:
    """Normalize a value for distinct-count comparison.

    None and NaN collapse to one "missing" token; numeric strings collapse to
    their float so a JSON round-trip ("0" vs 0) doesn't fake variation."""
    if v is None:
        return "__missing__"
    if isinstance(v, float) and math.isnan(v):
        return "__missing__"
    coerced = _num(v)
    return coerced


def _is_varying(values: Sequence[Any]) -> bool:
    """True when the value series has ≥ 2 distinct (normalized) values.

    An all-missing or single-valued (incl. all-zeros) series is "dead"
    (constant); any series with at least two distinct values is varying."""
    distinct: set[Any] = set()
    for v in values:
        distinct.add(_norm_value(v))
        if len(distinct) > 1:
            return True
    return False


def score_fidelity(
    sampled_rows: Sequence[tuple[Mapping[str, Any], float]],
    predict_fn: Callable[[Mapping[str, Any]], float],
    *,
    score_tol: float = DEFAULT_SCORE_TOL,
) -> int:
    """Count serving-fidelity mismatches over ``(feature_row, logged_score)``
    pairs. A re-score that raises counts as a mismatch (see module note).

    The row is passed to ``predict_fn`` EXACTLY as logged — no numeric
    coercion. The live scorer predicts on the raw feature_row, and lightgbm's
    frame build is dtype-sensitive: coercing ``dayofweek``/``hour_of_day``
    int → float shifted every BTC/SOL re-score by 3e-2..1.2e-1, reporting a
    100% "serving skew" that did not exist on the live side (the
    MB-20260720-LIVE-SERVING-PARITY-SKEW false alarm; live-verified 20/20
    exact matches on raw rows, trainer relay 2026-07-20)."""
    mismatched = 0
    for row, logged in sampled_rows:
        try:
            recomputed = float(predict_fn(dict(row)))
        except Exception:  # noqa: BLE001 — a row the artifact can't score IS a mismatch
            mismatched += 1
            continue
        if not math.isfinite(recomputed) or abs(recomputed - float(logged)) > score_tol:
            mismatched += 1
    return mismatched


def dead_features(
    live_rows: Sequence[Mapping[str, Any]],
    train_rows: Sequence[Mapping[str, Any]],
    *,
    exclude: frozenset[str] | set[str] = frozenset(),
    restrict_to: frozenset[str] | set[str] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The dead-feature split for a feature universe drawn from the TRAINING
    dataset's columns (minus ``exclude`` — target/time columns).

    When ``restrict_to`` is a non-empty set (the manifest's declared
    ``feature_columns``), the universe is exactly that set — the gate judges
    only the features the MODEL actually consumes. Without it, the full
    dataset universe flags label/side-stream columns the model never reads
    (the 2026-07-20 MES certification flagged forward_log_return + the whole
    macro block as "dead live" although none are in the head's 7 consumed
    features — false blockers on an otherwise-clean parity read).

    Returns ``(dead_live, dead_train)``:

    - ``dead_live`` — constant/all-zeros/missing across the live rows while
      varying in training (the ETH-xa class: the live pipeline never populates
      a feature the model trained on — includes the feature being entirely
      ABSENT from the live rows).
    - ``dead_train`` — constant in the training dataset while varying live
      (the training build zeroed a feature the live pipeline populates; the
      model learned nothing from it, so live variation is noise to it).

    A feature constant on BOTH sides is not flagged (a genuinely constant
    column, e.g. a symbol stamp, is consistent — just uninformative)."""
    if restrict_to:
        universe = set(restrict_to)
    else:
        universe = set()
        for tr in train_rows:
            universe.update(tr.keys())
    universe -= set(exclude)
    dead_live: list[str] = []
    dead_train: list[str] = []
    for feat in sorted(universe):
        live_vals = [row.get(feat) for row in live_rows]
        train_vals = [tr.get(feat) for tr in train_rows]
        live_varies = _is_varying(live_vals)
        train_varies = _is_varying(train_vals)
        if train_varies and not live_varies:
            dead_live.append(feat)
        elif live_varies and not train_varies:
            dead_train.append(feat)
    return tuple(dead_live), tuple(dead_train)


def _artifact_registered_at(entry: Any):
    """The CURRENT artifact's training-run timestamp (aware UTC) or ``None``.

    Resolved as the newest ``entry.runs[].at`` — the registry contract is
    that the newest run's ``model_state_path`` IS the entry's current
    artifact. Naive datetimes are treated as UTC. Never raises; a legacy
    entry with no run history resolves ``None`` (freshness scoping is then
    skipped — pre-scoping behaviour)."""
    try:
        from datetime import timezone as _tz

        run_times = [r.at for r in (getattr(entry, "runs", None) or ())]
        if not run_times:
            return None
        newest = max(
            t if t.tzinfo is not None else t.replace(tzinfo=_tz.utc)
            for t in run_times
        )
        return newest
    except Exception:  # noqa: BLE001 — unresolvable run history = no scoping
        return None


def _row_time(record: Any):
    """A shadow record's ``predicted_at_utc`` as an aware-UTC datetime, or
    ``None`` when unparseable (such a row never counts as artifact-fresh)."""
    try:
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        raw = str(record.predicted_at_utc).replace("Z", "+00:00")
        ts = _dt.fromisoformat(raw)
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=_tz.utc)
    except Exception:  # noqa: BLE001
        return None


def _load_train_rows(
    entry: Any, datasets_root: Path | str | None, *, max_rows: int,
) -> tuple[list[dict[str, Any]] | None, frozenset[str], frozenset[str]]:
    """Load (a bounded slice of) the entry's training dataset + the non-feature
    columns to exclude from the dead-feature universe + the manifest's declared
    ``feature_columns`` (empty when undeclared — the universe then falls back
    to the full dataset column set). ``(None, …, …)`` when the dataset can't
    be resolved/read (→ ``train_available=False``)."""
    if datasets_root is None:
        return None, frozenset(), frozenset()
    try:
        from ..experiments.runner import _load_jsonl
        from ..manifest import TrainingManifest

        manifest = TrainingManifest.from_dict(dict(entry.manifest))
        data_path = manifest.dataset.path_under(Path(datasets_root)) / "data.jsonl"
        if not data_path.is_file():
            return None, frozenset(), frozenset()
        rows = _load_jsonl(data_path)
        exclude: set[str] = set()
        for cfg in (manifest.trainer_config, manifest.evaluator_config):
            for key in ("target_column", "time_column"):
                val = (cfg or {}).get(key)
                if isinstance(val, str) and val:
                    exclude.add(val)
        consumed_raw = (manifest.trainer_config or {}).get("feature_columns")
        consumed = frozenset(
            c for c in consumed_raw if isinstance(c, str) and c
        ) if isinstance(consumed_raw, (list, tuple)) else frozenset()
        return list(rows[:max_rows]), frozenset(exclude), consumed
    except Exception:  # noqa: BLE001 — unreadable dataset = no train-side evidence
        return None, frozenset(), frozenset()


def compute_live_parity(
    entry: Any,
    *,
    shadow_log: Path | str,
    datasets_root: Path | str | None = None,
    registry_root: Path | str | None = None,
    sample_n: int = DEFAULT_SAMPLE_N,
    score_tol: float = DEFAULT_SCORE_TOL,
    max_train_rows: int = DEFAULT_MAX_TRAIN_ROWS,
    artifact_grace_s: float = DEFAULT_ARTIFACT_GRACE_S,
    dead_window_n: int = DEFAULT_DEAD_WINDOW_N,
) -> LiveParityResult:
    """Compute the live serving-mechanics parity evidence for one registry
    ``entry``. Never raises — any failure is folded into ``error`` so the
    gate reports ``insufficient_data`` (fail-safe, never a silent pass).

    **Artifact-freshness scoping (2026-07-20):** serving fidelity is judged
    only over rows logged AFTER the current artifact's training run
    (+ ``artifact_grace_s`` for mirror-sync/predictor-reload lag). Rows
    scored by a PREVIOUS nightly artifact mismatch today's re-score by
    construction and carry no skew information (the BTC/SOL "mismatch
    50/50" false blocker). Rows that DO postdate the artifact and still
    mismatch are the real serving-staleness signal (a live process holding
    an old model in memory). Dead-feature parity keeps the broad recent
    window — feature liveness is a pipeline property, not an artifact one."""
    model_id = str(entry.model_id)
    # 1. Live rows with feature_row (real-time only — backfill rows replay
    #    history through the current model and would trivially "match").
    #    A missing log is an explicit error (iter_records would silently
    #    yield nothing) — the gate must say WHY there is no evidence.
    if not Path(shadow_log).is_file():
        return LiveParityResult(
            model_id=model_id, score_tol=score_tol,
            error=f"shadow log not found: {shadow_log}",
        )
    try:
        from ..shadow.inspector import iter_records

        rows = [
            r for r in iter_records(shadow_log)
            if r.model_id == model_id
            and r.feature_row is not None
            and r.backfill_kind is None
        ]
    except Exception as exc:  # noqa: BLE001 — unreadable log → insufficient_data
        return LiveParityResult(
            model_id=model_id, score_tol=score_tol,
            error=f"shadow log unreadable: {exc}",
        )
    rows.sort(key=lambda r: r.predicted_at_utc)
    n_live = len(rows)
    recent = rows[-max(0, int(sample_n)):] if sample_n else []
    # Dead-feature liveness window: wider than the fidelity sample so calendar
    # features (dayofweek) span multiple distinct values — see
    # DEFAULT_DEAD_WINDOW_N. Falls back to `recent` if configured smaller.
    liveness = rows[-max(int(dead_window_n), max(0, int(sample_n))):]

    # Artifact-freshness scoping: fidelity samples only rows the CURRENT
    # artifact could have produced (see docstring). Legacy entries with no
    # run history sample the plain recent window.
    artifact_at = _artifact_registered_at(entry)
    artifact_at_iso: str | None = None
    n_fresh: int | None = None
    if artifact_at is not None:
        from datetime import timedelta as _td

        cutoff = artifact_at + _td(seconds=max(0.0, float(artifact_grace_s)))
        fresh = [
            r for r in rows
            if (ts := _row_time(r)) is not None and ts >= cutoff
        ]
        n_fresh = len(fresh)
        artifact_at_iso = artifact_at.isoformat()
        sampled = fresh[-max(0, int(sample_n)):] if sample_n else []
    else:
        sampled = recent

    # 2. Registered artifact (the exact loader the live runtime uses).
    try:
        from ..registry.model_registry import ModelRegistry
        from ..shadow import factory as _factory
        from ..shadow.factory import resolve_predictor

        root = Path(registry_root) if registry_root else _factory._resolve_default_registry_root()
        sp = resolve_predictor(model_id, ModelRegistry(root), log_path=None)
    except Exception as exc:  # noqa: BLE001 — model-load failure → insufficient_data
        return LiveParityResult(
            model_id=model_id, n_live_rows=n_live, score_tol=score_tol,
            error=f"model artifact load failed: {exc}",
        )

    # 3a. Serving fidelity over the (artifact-fresh) sampled rows.
    pairs = [(dict(r.feature_row or {}), float(r.score)) for r in sampled]
    n_mismatched = score_fidelity(pairs, sp.predict, score_tol=score_tol)

    # 3b. Dead-feature parity vs the training dataset, judged over the
    #     model's consumed feature_columns when the manifest declares them.
    #     Uses the broad recent window (not the artifact-fresh sample):
    #     feature liveness is a property of the live pipeline, so a fresh
    #     retrain must not blind the ETH-xa dead-feature detector.
    train_rows, exclude, consumed = _load_train_rows(
        entry, datasets_root, max_rows=max_train_rows,
    )
    if train_rows is None:
        return LiveParityResult(
            model_id=model_id, n_live_rows=n_live, n_sampled=len(pairs),
            n_mismatched=n_mismatched, score_tol=score_tol,
            train_available=False,
            artifact_at=artifact_at_iso, n_fresh_rows=n_fresh,
        )
    dead_live, dead_train = dead_features(
        [dict(r.feature_row or {}) for r in liveness], train_rows,
        exclude=exclude, restrict_to=consumed or None,
    )
    return LiveParityResult(
        model_id=model_id, n_live_rows=n_live, n_sampled=len(pairs),
        n_mismatched=n_mismatched, score_tol=score_tol,
        dead_live_features=dead_live, dead_train_features=dead_train,
        train_available=True,
        artifact_at=artifact_at_iso, n_fresh_rows=n_fresh,
    )
