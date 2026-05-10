"""Shadow-mode predictor factory (S-AI-WS7-PART-4) + inspector
(S-AI-WS8-PART-1) + drift detector (S-AI-WS8-PART-3)."""
from .drift import (
    DriftReport,
    Summary,
    compute_drift,
    histogram,
    interpret_ks,
    interpret_psi,
    ks_statistic,
    psi,
    summarize,
)
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
    "DriftReport",
    "LIVE_INFLUENCE_STAGES",
    "ModelStats",
    "ShadowFactoryError",
    "ShadowRecord",
    "Summary",
    "aggregate",
    "compute_drift",
    "filter_records",
    "format_inspect_table",
    "format_stats_table",
    "histogram",
    "interpret_ks",
    "interpret_psi",
    "iter_records",
    "ks_statistic",
    "psi",
    "record_from_dict",
    "resolve_predictor",
    "resolve_predictors",
    "summarize",
]
