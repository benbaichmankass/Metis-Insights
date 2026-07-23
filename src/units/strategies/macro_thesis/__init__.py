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
from .valuation_store import (  # noqa: F401
    latest_reads_for_symbol,
    read_latest_snapshots,
    read_snapshot_records,
    write_snapshots,
)
from .event_resolver import (  # noqa: F401
    VALID_ACTIONS,
    eval_predicate,
    resolve_action,
    resolve_event_for_theses,
)
from .event_store import (  # noqa: F401
    read_event_links,
    read_events,
    read_events_by_status,
    read_latest_events,
    resolve_all,
    write_event_links,
    write_events,
)
from .event_calendar import (  # noqa: F401
    build_scheduled_events,
    event_id_for,
    load_events_config,
    required_series as event_required_series,
    resolve_scheduled_event,
)
from .thesis import (  # noqa: F401
    CLOSE_REASONS,
    DIRECTIONS,
    EXPRESS_AS,
    FREE_SOURCES,
    STATUSES,
    TradeThesis,
    can_transition,
    new_thesis_id,
    transition,
    would_transition,
)
from .thesis_store import (  # noqa: F401
    read_latest_theses,
    read_open_theses,
    read_theses_by_status,
    read_thesis_records,
    write_theses,
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
    "write_snapshots",
    "read_snapshot_records",
    "read_latest_snapshots",
    "latest_reads_for_symbol",
    "VALID_ACTIONS",
    "eval_predicate",
    "resolve_action",
    "resolve_event_for_theses",
    "write_events",
    "read_events",
    "read_latest_events",
    "read_events_by_status",
    "write_event_links",
    "read_event_links",
    "resolve_all",
    "load_events_config",
    "event_id_for",
    "build_scheduled_events",
    "event_required_series",
    "resolve_scheduled_event",
    "TradeThesis",
    "STATUSES",
    "DIRECTIONS",
    "EXPRESS_AS",
    "CLOSE_REASONS",
    "FREE_SOURCES",
    "new_thesis_id",
    "can_transition",
    "transition",
    "would_transition",
    "write_theses",
    "read_thesis_records",
    "read_latest_theses",
    "read_theses_by_status",
    "read_open_theses",
]
