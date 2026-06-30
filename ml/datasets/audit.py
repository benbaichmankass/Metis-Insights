"""Build-time dataset label-quality + degeneracy audit (quarantine gate).

This is the **pre-training** counterpart to ``ml.datasets.validate`` (which
checks an on-disk artifact's *structural* integrity — metadata parses, row
count matches, schema types hold). ``audit_dataset`` instead inspects the
*content* of the rows a builder produced and flags the data-quality
pathologies that train cleanly yet are mysteriously NO_EDGE / f1=0 live:

  - **A fully (or near-fully) dead feature column** — every value 0 or NaN.
    This is the ``BL-20260628-XA-TRAINING-ZERO`` failure class: an all-zero
    ``xa_*`` cross-asset feature block was fed to the trainer because the
    offline peer-feature join produced nothing, so the head trained on a
    constant column and was NO_EDGE when scored live. A dead column carries
    no information; the booster can't learn from it and (worse) its presence
    masks the fact that the feature pipeline silently broke upstream.
  - **A constant feature column** (variance == 0 / a single unique value) —
    same information-free pathology by a different route.
  - **A degenerate (single-class) classification label** — the booster can
    only predict the one class it ever saw, so ``f1`` of every other class
    is 0. A dataset whose window happened to contain only ``range`` bars (no
    ``volatile``) is the canonical case.

When any of those fire, ``quarantine`` is ``True`` and ``ok`` is ``False`` —
the signal to the build/cycle wiring to **skip training this manifest** and
write a ``dataset_audit.jsonl`` row rather than train on dead data and
discover the problem only after a live soak.

Design constraints (match ``ml.datasets`` conventions):
  - **Pure function, no I/O, no side effects** — it only analyzes the rows it
    is handed. The caller owns reading the dataset and writing the audit log.
  - **Fail-permissive.** A malformed manifest, an unreadable column, or any
    per-feature computation error degrades that one field to ``flagged:false``
    with an explanatory reason rather than raising — an audit must never be the
    thing that crashes a build. (A genuinely dead column is still caught; the
    permissiveness is only for *our own* analysis errors.)
  - **Dependency-light.** stdlib only. ``rows`` may be a list-of-dicts (what
    ``DatasetBuilder`` writes to ``data.jsonl``) **or** a pandas DataFrame —
    the DataFrame is duck-typed via ``to_dict("records")`` so we never import
    pandas here.

The threshold for "dead" is a parameter (``dead_fraction_threshold``, default
``0.99``) so the build can tighten/loosen it; ``>=`` the threshold flags.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

# A feature whose zero-fraction OR nan-fraction is >= this is "dead".
DEFAULT_DEAD_FRACTION_THRESHOLD = 0.99

# Default label/target column names the trainers fall back to when the
# manifest's trainer_config omits target_column (mirrors
# ml.trainers.lightgbm_multiclass / regime_classifier → "regime_label").
_DEFAULT_TARGET_COLUMN = "regime_label"

# Target column names that are continuous (regression) in this repo's
# manifests — used as a hint so a *constant* regression target (e.g. an
# all-zero r_multiple window) is reported as a degenerate regression target
# rather than mis-detected as a single-class classification label. Heuristic
# value-based detection (``_looks_regression``) is the fallback when the
# manifest gives no signal.
_REGRESSION_TARGET_NAMES = frozenset({
    "r_multiple",
    "entry_slippage_bps",
    "total_pnl_pct",
    "decision_grade_score",
})


def _coerce_rows(rows: Any) -> list[dict]:
    """Normalize ``rows`` to a list of plain dicts.

    Accepts:
      - a list/tuple/iterable of mappings (the ``data.jsonl`` shape), or
      - a pandas DataFrame (duck-typed via ``to_dict("records")`` — no pandas
        import here, so this module stays dependency-light).
    Anything else yields an empty list (the caller's n_rows==0 path).
    """
    # DataFrame (or anything DataFrame-like): has to_dict + columns.
    to_dict = getattr(rows, "to_dict", None)
    if to_dict is not None and hasattr(rows, "columns"):
        try:
            recs = to_dict("records")
            return [dict(r) for r in recs]
        except Exception:
            return []
    if isinstance(rows, Mapping):
        # A single row passed bare — wrap it.
        return [dict(rows)]
    out: list[dict] = []
    try:
        for r in rows:
            if isinstance(r, Mapping):
                out.append(dict(r))
    except TypeError:
        return []
    return out


def _manifest_id(manifest: Any) -> str:
    """Best-effort model/manifest identity for the report header."""
    for attr in ("model_id",):
        val = getattr(manifest, attr, None)
        if isinstance(val, str) and val.strip():
            return val
    if isinstance(manifest, Mapping):
        for key in ("model_id", "id", "name"):
            val = manifest.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return "unknown"


def _trainer_config(manifest: Any) -> Mapping[str, Any]:
    cfg = getattr(manifest, "trainer_config", None)
    if isinstance(cfg, Mapping):
        return cfg
    if isinstance(manifest, Mapping):
        sub = manifest.get("trainer_config")
        if isinstance(sub, Mapping):
            return sub
    return {}


def _resolve_feature_columns(manifest: Any) -> list[str]:
    """Resolve the declared feature columns from a manifest.

    Looks under ``trainer_config.feature_columns`` (the canonical location the
    trainers read) and, as a fallback for minimal/hand-built manifests, a
    top-level ``feature_columns`` / ``features`` key. Returns ``[]`` when none
    is declared (the audit then has no features to score, not an error).
    """
    cfg = _trainer_config(manifest)
    raw = cfg.get("feature_columns")
    if raw is None and isinstance(manifest, Mapping):
        raw = manifest.get("feature_columns")
        if raw is None:
            raw = manifest.get("features")
    if raw is None:
        # also accept a top-level attribute on dataclass-like manifests
        raw = getattr(manifest, "feature_columns", None) or getattr(
            manifest, "features", None
        )
    if not raw:
        return []
    try:
        return [str(c) for c in raw]
    except TypeError:
        return []


def _resolve_target_column(manifest: Any) -> str:
    """Resolve the label/target column name from a manifest."""
    cfg = _trainer_config(manifest)
    for key in ("target_column", "label", "target"):
        val = cfg.get(key)
        if isinstance(val, str) and val.strip():
            return val
    if isinstance(manifest, Mapping):
        for key in ("target_column", "label", "target"):
            val = manifest.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return _DEFAULT_TARGET_COLUMN


def _manifest_task_hint(manifest: Any, target: str) -> str | None:
    """Resolve the supervised task kind from the manifest, or None.

    Returns ``"regression"`` / ``"classification"`` when the manifest gives a
    clear signal (evaluator / trainer qualname / model_family / a known
    regression target name), else ``None`` so the caller falls back to value-
    based detection. Fail-permissive — any error yields ``None``.
    """
    try:
        if target in _REGRESSION_TARGET_NAMES:
            return "regression"

        def _attr(name: str) -> str:
            val = getattr(manifest, name, None)
            if val is None and isinstance(manifest, Mapping):
                val = manifest.get(name)
            return str(val).lower() if isinstance(val, str) else ""

        blob = " ".join(
            _attr(k) for k in ("evaluator", "trainer", "model_family")
        )
        if not blob.strip():
            return None
        if "regression" in blob:
            return "regression"
        if (
            "classification" in blob
            or "multiclass" in blob
            or "regime_classifier" in blob
        ):
            return "classification"
        return None
    except Exception:
        return None


def _to_float(v: Any) -> float | None:
    """Best-effort numeric coercion; None for non-numeric / NaN / inf."""
    if v is None or isinstance(v, bool):
        # bool is intentionally excluded — treat True/False as categorical,
        # not numeric, so a boolean feature column isn't mis-scored.
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


@dataclass
class FeatureAudit:
    name: str
    n: int
    zero_fraction: float | None
    nan_fraction: float | None
    variance: float | None
    n_unique: int
    flagged: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LabelAudit:
    name: str
    kind: str  # "classification" | "regression" | "empty" | "unknown"
    n: int
    # classification
    balance: dict[str, int] = field(default_factory=dict)
    n_classes: int = 0
    # regression
    stats: dict[str, float] | None = None
    flagged: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _audit_feature(
    name: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    dead_fraction_threshold: float,
) -> FeatureAudit:
    """Compute per-column dead/constant diagnostics for one feature.

    Fail-permissive: any internal error degrades this column to
    ``flagged:false`` with a reason, never raises.
    """
    n = len(rows)
    try:
        present_count = 0  # rows where the column key exists at all
        nan_count = 0  # present-but-null/non-numeric/missing → "nan-ish"
        zero_count = 0  # numeric and == 0.0
        numeric_vals: list[float] = []
        seen_raw: set[Any] = set()
        seen_overflow = False

        for r in rows:
            has = name in r
            if has:
                present_count += 1
            raw = r.get(name)
            # uniqueness over hashable raw values (categorical-aware)
            if not seen_overflow:
                try:
                    seen_raw.add(raw)
                    if len(seen_raw) > 1024:
                        seen_overflow = True
                except TypeError:
                    seen_overflow = True
            fv = _to_float(raw)
            if fv is None:
                # missing key, explicit null, or non-numeric → counts toward
                # "nan_fraction" (information the booster can't use as-is).
                nan_count += 1
                continue
            numeric_vals.append(fv)
            if fv == 0.0:
                zero_count += 1

        if n == 0:
            return FeatureAudit(
                name=name, n=0, zero_fraction=None, nan_fraction=None,
                variance=None, n_unique=0, flagged=False,
                reason="no rows to audit",
            )

        zero_fraction = zero_count / n
        nan_fraction = nan_count / n
        n_unique = (-1 if seen_overflow else len(seen_raw))

        # population variance over the numeric values present.
        variance: float | None
        if len(numeric_vals) >= 1:
            mean = sum(numeric_vals) / len(numeric_vals)
            variance = sum((x - mean) ** 2 for x in numeric_vals) / len(
                numeric_vals
            )
        else:
            variance = None

        # ---- flagging ----
        reasons: list[str] = []
        # 1. fully/near-fully dead by zeros
        if zero_fraction >= dead_fraction_threshold:
            reasons.append(
                f"zero_fraction {zero_fraction:.4f} >= {dead_fraction_threshold} "
                "(dead/degenerate column — see BL-20260628-XA-TRAINING-ZERO)"
            )
        # 2. fully/near-fully missing/non-numeric
        if nan_fraction >= dead_fraction_threshold:
            reasons.append(
                f"nan_fraction {nan_fraction:.4f} >= {dead_fraction_threshold} "
                "(column absent / null / non-numeric for ~all rows)"
            )
        # 3. constant column (zero variance among >=2 numeric values), but
        #    don't double-flag the all-zero case already caught above.
        if (
            variance is not None
            and len(numeric_vals) >= 2
            and variance == 0.0
            and zero_fraction < dead_fraction_threshold
        ):
            reasons.append("variance == 0 (constant feature)")

        flagged = bool(reasons)
        return FeatureAudit(
            name=name,
            n=n,
            zero_fraction=round(zero_fraction, 6),
            nan_fraction=round(nan_fraction, 6),
            variance=(round(variance, 10) if variance is not None else None),
            n_unique=n_unique,
            flagged=flagged,
            reason=("; ".join(reasons) if reasons else None),
        )
    except Exception as exc:  # pragma: no cover - defensive
        # fail-permissive: our own analysis error must not flag/crash.
        return FeatureAudit(
            name=name, n=n, zero_fraction=None, nan_fraction=None,
            variance=None, n_unique=0, flagged=False,
            reason=f"audit-error (skipped): {type(exc).__name__}: {exc}",
        )


def _looks_regression(values: Sequence[Any]) -> bool:
    """Heuristic: a target is regression-like when its non-null values are
    numeric (float-coercible) AND there are many distinct values relative to
    the sample (so a small-int class code like 0/1/2 stays classification).
    """
    nums = [_to_float(v) for v in values]
    present = [v for v in nums if v is not None]
    if not present:
        return False
    # if any present value is non-numeric, it's categorical/classification
    non_numeric = any(_to_float(v) is None for v in values if v is not None)
    if non_numeric:
        return False
    distinct = len(set(present))
    # treat as regression only when it's clearly continuous: > 12 distinct
    # numeric values AND not all (near-)integers, OR > 20 distinct.
    near_int = all(abs(x - round(x)) < 1e-9 for x in present)
    if distinct > 20:
        return True
    if distinct > 12 and not near_int:
        return True
    return False


def _audit_label(
    name: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    task_hint: str | None = None,
) -> LabelAudit:
    """Class-balance / distribution audit of the label/target column.

    Flags a **single-class classification** label (the f1=0 degenerate case).
    For a regression target, reports basic distribution stats and flags a
    constant target. ``task_hint`` (``"regression"``/``"classification"`` from
    the manifest) overrides the value-based detection so a *constant*
    regression target isn't mis-read as a single-class label. Fail-permissive.
    """
    n = len(rows)
    try:
        raw_vals = [r.get(name) for r in rows]
        present = [v for v in raw_vals if v is not None and v != ""]
        if not present:
            return LabelAudit(
                name=name, kind="empty", n=n, flagged=True,
                reason=f"label column {name!r} is null/absent for all rows",
            )

        if task_hint == "regression" or (
            task_hint is None and _looks_regression(present)
        ):
            nums = [x for x in (_to_float(v) for v in present) if x is not None]
            if not nums:
                return LabelAudit(
                    name=name, kind="unknown", n=n, flagged=True,
                    reason="regression target coerced to no numeric values",
                )
            mean = sum(nums) / len(nums)
            var = sum((x - mean) ** 2 for x in nums) / len(nums)
            stats = {
                "count": len(nums),
                "mean": round(mean, 8),
                "std": round(math.sqrt(var), 8),
                "min": round(min(nums), 8),
                "max": round(max(nums), 8),
                "n_unique": len(set(nums)),
            }
            constant = len(set(nums)) <= 1
            return LabelAudit(
                name=name, kind="regression", n=n, stats=stats,
                n_classes=len(set(nums)), flagged=constant,
                reason=("constant regression target (zero variance)"
                        if constant else None),
            )

        # classification: count class frequencies (string-normalized like the
        # trainers do — str(label).strip()).
        balance: dict[str, int] = {}
        for v in present:
            key = str(v).strip()
            if not key:
                continue
            balance[key] = balance.get(key, 0) + 1
        n_classes = len(balance)
        flagged = n_classes <= 1
        reason = None
        if n_classes == 0:
            flagged = True
            reason = f"label column {name!r} has no non-empty classes"
        elif n_classes == 1:
            only = next(iter(balance))
            reason = (
                f"single-class label (all {balance[only]} rows == {only!r}) — "
                "degenerate target, every other class scores f1=0"
            )
        return LabelAudit(
            name=name, kind="classification", n=n, balance=balance,
            n_classes=n_classes, flagged=flagged, reason=reason,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return LabelAudit(
            name=name, kind="unknown", n=n, flagged=False,
            reason=f"audit-error (skipped): {type(exc).__name__}: {exc}",
        )


def audit_dataset(
    rows: Any,
    manifest: Any,
    *,
    dead_fraction_threshold: float = DEFAULT_DEAD_FRACTION_THRESHOLD,
) -> dict[str, Any]:
    """Audit a freshly-built dataset for the label-quality + degeneracy
    pathologies that train cleanly yet go NO_EDGE / f1=0 live.

    Parameters
    ----------
    rows:
        The dataset rows. Either a list-of-dicts (the ``data.jsonl`` shape a
        ``DatasetBuilder`` writes) or a pandas DataFrame (duck-typed, no pandas
        import). One element per training row.
    manifest:
        The training manifest. Either a ``ml.manifest.TrainingManifest`` (its
        ``trainer_config.feature_columns`` / ``target_column`` are read) or a
        plain mapping carrying ``trainer_config`` — or, for minimal/hand-built
        manifests, top-level ``features``/``feature_columns`` and
        ``label``/``target`` keys.
    dead_fraction_threshold:
        A feature whose ``zero_fraction`` OR ``nan_fraction`` is ``>=`` this is
        flagged dead and triggers quarantine. Default ``0.99``.

    Returns
    -------
    dict
        A structured report::

            {
              "ok": bool,                 # False iff quarantine
              "manifest": "<model_id>",
              "n_rows": int,
              "dead_fraction_threshold": float,
              "features": [
                {name, zero_fraction, nan_fraction, variance, n_unique,
                 flagged, reason, n}, ...
              ],
              "label": {name, kind, balance|stats, n_classes, flagged, reason, n},
              "flags": ["<human-readable flag>", ...],
              "quarantine": bool,         # any feature OR the label flagged
            }

        ``ok`` is the inverse of ``quarantine``. When ``quarantine`` is True the
        build wiring should SKIP training this manifest and log the report to
        ``runtime_logs/trainer/dataset_audit.jsonl`` (it is the
        ``BL-20260628-XA-TRAINING-ZERO`` / single-class-label guard).

    This function never raises on bad data — analysis errors degrade the
    affected field to ``flagged:false`` with an explanatory ``reason``.
    """
    record_rows = _coerce_rows(rows)
    n_rows = len(record_rows)
    manifest_id = _manifest_id(manifest)
    feature_cols = _resolve_feature_columns(manifest)
    target_col = _resolve_target_column(manifest)

    flags: list[str] = []

    feature_reports: list[FeatureAudit] = []
    for col in feature_cols:
        fa = _audit_feature(
            col, record_rows, dead_fraction_threshold=dead_fraction_threshold
        )
        feature_reports.append(fa)
        if fa.flagged:
            flags.append(f"feature {col!r}: {fa.reason}")

    label_report = _audit_label(
        target_col, record_rows, task_hint=_manifest_task_hint(manifest, target_col)
    )
    if label_report.flagged:
        flags.append(f"label {target_col!r}: {label_report.reason}")

    # An empty dataset is itself a quarantine condition — there is nothing to
    # train, and silently training on 0 rows is the same class of failure.
    if n_rows == 0:
        flags.append("dataset has 0 rows")

    quarantine = bool(flags)

    return {
        "ok": not quarantine,
        "manifest": manifest_id,
        "n_rows": n_rows,
        "dead_fraction_threshold": dead_fraction_threshold,
        "features": [fa.to_dict() for fa in feature_reports],
        "label": label_report.to_dict(),
        "flags": flags,
        "quarantine": quarantine,
    }
