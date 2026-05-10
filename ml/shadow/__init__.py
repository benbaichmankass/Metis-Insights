"""Shadow-mode predictor factory (S-AI-WS7-PART-4) + inspector
(S-AI-WS8-PART-1)."""
from .factory import (
    DEFAULT_LOG_PATH,
    DEFAULT_REGISTRY_ROOT,
    LIVE_INFLUENCE_STAGES,
    ShadowFactoryError,
    resolve_predictor,
    resolve_predictors,
)
from .inspector import (
    ModelStats,
    ShadowRecord,
    aggregate,
    filter_records,
    format_inspect_table,
    format_stats_table,
    iter_records,
    record_from_dict,
)

__all__ = [
    "DEFAULT_LOG_PATH",
    "DEFAULT_REGISTRY_ROOT",
    "LIVE_INFLUENCE_STAGES",
    "ModelStats",
    "ShadowFactoryError",
    "ShadowRecord",
    "aggregate",
    "filter_records",
    "format_inspect_table",
    "format_stats_table",
    "iter_records",
    "record_from_dict",
    "resolve_predictor",
    "resolve_predictors",
]
