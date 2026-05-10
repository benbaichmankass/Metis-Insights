"""Config-driven shadow predictor factory (S-AI-WS7-PART-4).

Resolves a list of model_ids from `config/strategies.yaml` (or any
caller) into a list of `ShadowPredictor` instances. Each model_id
is looked up in the model registry; the registry entry's
`target_deployment_stage` gates whether the model is allowed to
run in shadow mode at all.

Stage gate (`LIVE_INFLUENCE_STAGES`):

- `shadow`              ✅ allowed — that's the whole point.
- `advisory`            ✅ allowed — advisory mode displays the
                         score; running it as a shadow side-channel
                         is a strict subset.
- `limited_live`        ✅ allowed — same reasoning.
- `live_approved`       ✅ allowed — running an approved model in
                         shadow against another strategy is fine.
- `research_only`       ❌ refused — model has not been promoted
                         past the leakage / walk-forward gates.
- `candidate`           ❌ refused — same.
- `backtest_approved`   ❌ refused — backtest is not yet shadow.

Refusal raises `ShadowFactoryError`. Per-model errors are
collected when `resolve_predictors()` is called with multiple ids
so one bad model_id doesn't poison the whole list.

Defaults:

- `DEFAULT_REGISTRY_ROOT` = `./ml/registry-store` (same as the CLI
  default in `ml/cli.py`).
- `DEFAULT_LOG_PATH`      = `runtime_logs/shadow_predictions.jsonl`.
"""
from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Iterable, List

from ..predictors.base import Predictor
from ..predictors.shadow import ShadowPredictor
from ..registry.model_registry import ModelRegistry, RegistryEntry, RegistryError

DEFAULT_REGISTRY_ROOT = Path("./ml/registry-store")
DEFAULT_LOG_PATH = Path("runtime_logs/shadow_predictions.jsonl")

LIVE_INFLUENCE_STAGES: frozenset[str] = frozenset(
    {"shadow", "advisory", "limited_live", "live_approved"}
)

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


def _load_model_state(model_state_path: str | Path) -> dict:
    p = Path(model_state_path)
    if not p.is_file():
        raise ShadowFactoryError(
            f"model_state_path not found at {p}"
        )
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowFactoryError(
            f"failed to read model state at {p}: {exc}"
        ) from exc


def _check_stage(entry: RegistryEntry) -> None:
    if entry.target_deployment_stage not in LIVE_INFLUENCE_STAGES:
        raise ShadowFactoryError(
            f"model {entry.model_id!r} is at stage "
            f"{entry.target_deployment_stage!r}; refusing to load — "
            f"promote past `backtest_approved` first. Allowed stages: "
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
    2. Verify `target_deployment_stage` is in `LIVE_INFLUENCE_STAGES`
       (refuse to load a `research_only` / `candidate` /
       `backtest_approved` model — operator should promote first).
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
    state = _load_model_state(entry.model_state_path)
    predictor_cls = _resolve_predictor_class(state.get("trainer", ""))
    base_predictor = predictor_cls(state)
    return ShadowPredictor(
        base_predictor,
        model_id=entry.model_id,
        stage=entry.target_deployment_stage,
        log_path=log_path,
    )


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
