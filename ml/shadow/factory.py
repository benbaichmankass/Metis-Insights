"""Config-driven shadow predictor factory (S-AI-WS7-PART-4).

Resolves a list of model_ids from `config/strategies.yaml` (or any
caller) into a list of `ShadowPredictor` instances. Each model_id
is looked up in the model registry; the registry entry's
`target_deployment_stage` gates whether the model is allowed to
run in shadow mode at all.

Stage gate (`LIVE_INFLUENCE_STAGES`), over the 3 canonical stages
(stored stages and legacy aliases are normalized via `canonical_stage`):

- `shadow`     ✅ allowed — the default stage for any registered
                 model (2026-05-19 default flip). Auto-wires onto
                 every strategy via `discover_shadow_stage_model_ids()`
                 + `Coordinator._get_shadow_predictors` when the
                 strategy YAML omits `shadow_model_ids`.
- `advisory`   ✅ allowed — advisory mode displays/uses the score;
                 running it as a shadow side-channel is a strict
                 subset. (Legacy `limited_live` / `live_approved`
                 normalize to `advisory`, so they still load.)
- `candidate`  ❌ refused — pre-shadow. (Legacy `research_only` /
                 `backtest_approved` normalize to `candidate`, so
                 they are likewise refused.)

Refusal raises `ShadowFactoryError`. Per-model errors are
collected when `resolve_predictors()` is called with multiple ids
so one bad model_id doesn't poison the whole list.

Defaults:

- `DEFAULT_REGISTRY_ROOT` — resolved at import time from the
  environment so the factory automatically lands on the right path
  without per-caller plumbing. Resolution order:

  1. `ML_REGISTRY_ROOT` env var (explicit override; used by tests
     and by anyone who wants to force a non-default location).
  2. `$DATA_DIR/runtime_logs/trainer_mirror/models` if `DATA_DIR`
     is set and absolute. This is the canonical layout on the live
     VM, where the systemd drop-in `deploy/dropins/data-dir.conf`
     pins `DATA_DIR=/data/bot-data` and the trainer's
     `scripts/ops/publish_trainer_mirror.sh` syncs per-model
     registry JSONs into a `models/` subdirectory inside the same
     trainer mirror the dashboard reads from at the parent path.
     The subdirectory keeps `.list()` glob calls from picking up
     sibling artifacts like `trainer_status.json`, which would
     fail `RegistryEntry.from_dict()`.
  3. `./ml/registry-store` — local-dev / trainer-VM fallback,
     where `python -m ml train` writes per-model JSONs directly.

- `DEFAULT_LOG_PATH`      = `runtime_logs/shadow_predictions.jsonl`.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
from pathlib import Path
from typing import Iterable, List

from ..manifest import canonical_stage
from ..predictors.base import Predictor
from ..predictors.shadow import ShadowPredictor
from ..registry.model_registry import ModelRegistry, RegistryEntry, RegistryError


def _resolve_default_registry_root() -> Path:
    """Pick the registry root for the running environment.

    See module docstring for the resolution order. Splitting this out
    of a module-level constant lets tests monkeypatch ``os.environ``
    and call ``_resolve_default_registry_root()`` directly to verify
    each branch.
    """
    explicit = os.environ.get("ML_REGISTRY_ROOT")
    if explicit:
        return Path(explicit)
    data_dir = os.environ.get("DATA_DIR")
    if data_dir and os.path.isabs(data_dir):
        return Path(data_dir) / "runtime_logs" / "trainer_mirror" / "models"
    return Path("./ml/registry-store")


DEFAULT_REGISTRY_ROOT = _resolve_default_registry_root()
DEFAULT_LOG_PATH = Path("runtime_logs/shadow_predictions.jsonl")

# Canonical stages a model may be loaded as a shadow predictor at:
# `shadow` (observe-only) and `advisory` (influence) — both run as a
# logging side-channel. `candidate` (pre-shadow) is refused. Comparison is
# done on the canonical form, so an entry stored as a legacy alias
# (`limited_live` / `live_approved` → advisory) still loads.
LIVE_INFLUENCE_STAGES: frozenset[str] = frozenset({"shadow", "advisory"})

_DEFAULT_LOGGER = logging.getLogger(__name__)


class ShadowFactoryError(RuntimeError):
    """A model_id could not be turned into a ShadowPredictor."""


def _resolve_predictor_class(trainer_qualname: str) -> type[Predictor]:
    """Resolve `state['trainer']` qualname → `cls.PREDICTOR_CLASS`.

    Mirrors the logic in `ml.evaluators.base.Evaluator._resolve_predictor`
    so the factory and the evaluator stay in sync. Pulled into its
    own helper here so the factory doesn't depend on the evaluator
    class hierarchy.
    """
    if not trainer_qualname or "." not in trainer_qualname:
        raise ShadowFactoryError(
            f"model_state['trainer'] must be a fully-qualified Python "
            f"class qualname; got {trainer_qualname!r}"
        )
    module_name, _, attr = trainer_qualname.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ShadowFactoryError(
            f"trainer module {module_name!r} not importable: {exc}"
        ) from exc
    trainer_cls = getattr(module, attr, None)
    if trainer_cls is None:
        raise ShadowFactoryError(
            f"trainer class {trainer_qualname!r} not found"
        )
    predictor_cls = getattr(trainer_cls, "PREDICTOR_CLASS", None)
    if predictor_cls is None:
        raise ShadowFactoryError(
            f"trainer {trainer_qualname!r} has no PREDICTOR_CLASS"
        )
    return predictor_cls


def _load_model_state(
    model_state_path: str | Path,
    *,
    registry_root: Path | None = None,
) -> dict:
    """Resolve and load a model_state.json file.

    The registry entry stores ``model_state_path`` as an absolute path
    relative to the trainer VM's filesystem (e.g.
    ``/home/ubuntu/ict-trading-bot/ml/experiments-runs/<model_id>/<run_id>/model_state.json``).
    When the same registry entry is consumed on the live VM via the
    trainer mirror, that absolute path does not exist — the equivalent
    file is at ``<registry_root>/../experiments-runs/<model_id>/<run_id>/model_state.json``
    instead.

    Resolution order:

    1. The literal ``model_state_path`` from the entry (works on the
       trainer VM where it was registered).
    2. Strip everything up to and including ``experiments-runs/``,
       then resolve the suffix under ``<registry_root>/../experiments-runs/``.
       Works on the live VM where the mirror lives next to the
       ``models/`` subdir.

    Raises ``ShadowFactoryError`` if both attempts fail. The fallback
    is silent on success — the strategy doesn't need to know which
    branch matched.
    """
    p = Path(model_state_path)
    if p.is_file():
        candidate = p
    else:
        candidate = _resolve_state_path_via_mirror(p, registry_root)
        if candidate is None or not candidate.is_file():
            raise ShadowFactoryError(
                f"model_state_path not found at {p}"
                + (f" (also tried mirror fallback {candidate})" if candidate else "")
            )
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowFactoryError(
            f"failed to read model state at {candidate}: {exc}"
        ) from exc


def _resolve_state_path_via_mirror(
    original: Path,
    registry_root: Path | None,
) -> Path | None:
    """Map a trainer-VM absolute path to its live-VM mirror location.

    Looks for ``experiments-runs`` in the path's parts and rebuilds
    the suffix under ``<registry_root>/../experiments-runs/``. Returns
    ``None`` when ``registry_root`` is unset or the path has no
    ``experiments-runs`` segment.
    """
    if registry_root is None:
        return None
    parts = original.parts
    try:
        idx = parts.index("experiments-runs")
    except ValueError:
        return None
    suffix = Path(*parts[idx + 1 :])
    return registry_root.parent / "experiments-runs" / suffix


def _check_stage(entry: RegistryEntry) -> None:
    try:
        stage = canonical_stage(entry.target_deployment_stage)
    except ValueError as exc:
        raise ShadowFactoryError(
            f"model {entry.model_id!r} has unrecognized stage "
            f"{entry.target_deployment_stage!r}: {exc}"
        ) from exc
    if stage not in LIVE_INFLUENCE_STAGES:
        raise ShadowFactoryError(
            f"model {entry.model_id!r} is at stage "
            f"{entry.target_deployment_stage!r}; refusing to load — "
            f"promote to `shadow` first. Allowed stages: "
            f"{sorted(LIVE_INFLUENCE_STAGES)}"
        )


def resolve_predictor(
    model_id: str,
    registry: ModelRegistry,
    *,
    log_path: Path | None = None,
) -> ShadowPredictor:
    """Resolve a single model_id to a `ShadowPredictor`.

    Steps:

    1. Look up `model_id` in the registry.
    2. Verify the canonical `target_deployment_stage` is in
       `LIVE_INFLUENCE_STAGES` (refuse to load a `candidate`/pre-shadow
       model — operator should promote to `shadow` first).
    3. Load model state JSON from `entry.model_state_path`.
    4. Resolve the predictor class via `state['trainer']` qualname.
    5. Instantiate the base predictor and wrap in `ShadowPredictor`.

    `log_path` defaults to `DEFAULT_LOG_PATH`. Pass `None` if the
    caller wants to suppress audit logging.
    """
    try:
        entry = registry.get(model_id)
    except RegistryError as exc:
        raise ShadowFactoryError(
            f"model_id {model_id!r} not found in registry: {exc}"
        ) from exc
    _check_stage(entry)
    state = _load_model_state(entry.model_state_path, registry_root=registry.root)
    predictor_cls = _resolve_predictor_class(state.get("trainer", ""))
    base_predictor = predictor_cls(state)
    return ShadowPredictor(
        base_predictor,
        model_id=entry.model_id,
        stage=entry.target_deployment_stage,
        log_path=log_path,
    )


# A model trained across symbols (`symbol_scope: all`) is symbol-agnostic —
# it auto-wires onto a strategy regardless of that strategy's instrument.
_SYMBOL_AGNOSTIC = "ALL"


def model_symbol_scope(entry: RegistryEntry) -> str | None:
    """Return the model's declared training-symbol scope, normalized upper-case.

    Reads `manifest.dataset.symbol_scope` (the canonical field; falls back to
    `dataset.symbol` then a top-level `symbol_scope`). Returns `"ALL"` for a
    symbol-agnostic model (trained across symbols), a specific symbol (e.g.
    `"BTCUSDT"` / `"MES"`), or `None` when the manifest declares no scope at
    all (treated as agnostic by the symbol filter, fail-permissive).
    """
    manifest = getattr(entry, "manifest", None) or {}
    dataset = manifest.get("dataset") or {}
    scope = (
        dataset.get("symbol_scope")
        or dataset.get("symbol")
        or manifest.get("symbol_scope")
    )
    if scope is None:
        return None
    return str(scope).strip().upper()


def discover_shadow_stage_model_ids(
    registry: ModelRegistry, *, symbol: str | None = None,
) -> List[str]:
    """Return shadow-stage model_ids, optionally filtered to one symbol.

    Used by the coordinator's auto-wire path (2026-05-19): when a
    strategy's `shadow_model_ids` is missing/None (the default),
    every shadow-stage model in the registry is attached as a
    shadow predictor on that strategy's signals. The influence stage
    (`advisory`; legacy `limited_live` / `live_approved` normalize to it)
    is excluded — those models have promotion responsibilities of their own and
    shouldn't be silently re-purposed as a per-strategy shadow
    side-channel.

    **Symbol-aware filtering (2026-06-18).** When `symbol` is given, the
    auto-wire set is restricted to models whose declared `symbol_scope`
    is that symbol OR is symbol-agnostic (`"ALL"` / unset). This stops a
    strategy on symbol S from auto-wiring a model trained on a *different*
    specific symbol — which would score it out-of-distribution and pollute
    that model's shadow track record (drift / live-agreement promotion
    stats). `symbol=None` (the default) preserves the legacy
    all-shadow-models behaviour for callers without a single-symbol context
    (e.g. the historical backfill, which scores every model on every trade
    via its own per-trade symbol join).

    Strategies that opt out by setting `shadow_model_ids: []` get
    an empty list, not the auto-discovered set; strategies with an
    explicit non-empty list get exactly that list. The auto path
    only fires when the field is absent or `None`.

    Ordered alphabetically for stable cache identity across reloads.
    """
    want = symbol.strip().upper() if symbol else None
    ids: List[str] = []
    for entry in registry.list():
        if entry.target_deployment_stage != "shadow":
            continue
        if want is not None:
            scope = model_symbol_scope(entry)
            if scope is not None and scope != _SYMBOL_AGNOSTIC and scope != want:
                continue
        ids.append(entry.model_id)
    return sorted(ids)


def resolve_predictors(
    model_ids: Iterable[str],
    registry: ModelRegistry,
    *,
    log_path: Path | None = DEFAULT_LOG_PATH,
    logger: logging.Logger | None = None,
    strict: bool = False,
) -> List[ShadowPredictor]:
    """Resolve a list of model_ids; per-model errors are logged.

    By default (`strict=False`), a `ShadowFactoryError` for one
    `model_id` is logged and skipped — the other model_ids continue
    to resolve. This matches the helper's per-call-isolation
    contract (one bad model never blocks others). Pass
    `strict=True` to re-raise on the first error (useful in
    operator scripts that want to fail fast on misconfiguration).

    Returns the list in input order, with skipped entries omitted.
    """
    log = logger if logger is not None else _DEFAULT_LOGGER
    out: List[ShadowPredictor] = []
    for mid in model_ids:
        try:
            out.append(
                resolve_predictor(
                    mid, registry, log_path=log_path,
                )
            )
        except ShadowFactoryError as exc:
            if strict:
                raise
            log.warning(
                "shadow_factory_skipped model_id=%s err=%s", mid, exc
            )
    return out
