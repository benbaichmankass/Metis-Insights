"""M28 — Macro/Value Speculation Sleeve.

A thesis-driven, event-aware, weeks-horizon, symbol-agnostic speculation sleeve
(design of record: ``docs/research/M28-macro-value-speculation-DESIGN.md``;
schema: ``docs/research/M28-P0-schema-2026-07-22.md``).

This package is built up phase by phase. P1's first brick is :mod:`valuation` —
the pure, offline-testable "is this asset cheap vs its own history" value core
that the FRED-derived feed (a later P1 increment) wraps to write point-in-time
``valuation_snapshots``. Nothing here touches a live order path — the sleeve
executor (P3+) is gated and observe-only first.
"""

from .valuation import (  # noqa: F401
    ValueRead,
    credit_spread,
    equity_risk_premium,
    gold_silver_ratio,
    real_yield,
    term_slope,
    value_read,
    value_to_direction,
)
from .fred_adapter import (  # noqa: F401
    fred_fetch_and_history,
    metric_histories,
    parse_fredgraph_csv,
)
from .valuation_feed import (  # noqa: F401
    build_valuation_reads,
    compute_metric,
    load_valuation_config,
    required_series,
    run_valuation_feed,
)

__all__ = [
    "ValueRead",
    "equity_risk_premium",
    "real_yield",
    "gold_silver_ratio",
    "credit_spread",
    "term_slope",
    "value_read",
    "value_to_direction",
    "load_valuation_config",
    "compute_metric",
    "build_valuation_reads",
    "required_series",
    "run_valuation_feed",
    "parse_fredgraph_csv",
    "metric_histories",
    "fred_fetch_and_history",
]
