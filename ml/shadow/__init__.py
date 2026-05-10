"""Shadow-mode predictor factory (S-AI-WS7-PART-4)."""
from .factory import (
    DEFAULT_LOG_PATH,
    DEFAULT_REGISTRY_ROOT,
    LIVE_INFLUENCE_STAGES,
    ShadowFactoryError,
    resolve_predictor,
    resolve_predictors,
)

__all__ = [
    "DEFAULT_LOG_PATH",
    "DEFAULT_REGISTRY_ROOT",
    "LIVE_INFLUENCE_STAGES",
    "ShadowFactoryError",
    "resolve_predictor",
    "resolve_predictors",
]
