"""Promotion checklist (WS4).

Documented hard gates per registry transition. The CLI's `promote`
subcommand surfaces these and refuses to act unless the operator
explicitly acknowledges them via `--gates-acknowledged`. The reason
string on the resulting `StatusEvent` is the durable record of which
gates were satisfied.

The gates here are documentation; gate evidence (e.g. "leakage_test
results at experiments/<id>/leakage.json") lives in the experiment
artifacts and is referenced from the StatusEvent.reason.
"""
from __future__ import annotations

from typing import Mapping

PROMOTION_GATES: Mapping[tuple[str, str], tuple[str, ...]] = {
    ("candidate", "paper"): (
        "leakage_test_passed",
        "walk_forward_evaluation_complete",
    ),
    ("candidate", "champion"): (
        "comparison_against_incumbent_complete",
        "operator_explicit_approval",
    ),
    ("paper", "advisory"): (
        "transaction_cost_evaluation_complete",
        "metrics_beat_heuristic_baseline",
    ),
    ("advisory", "live-approved"): (
        "shadow_mode_clean_soak_at_least_7d",
        "operator_explicit_approval",
        "rollback_plan_documented",
    ),
    ("live-approved", "champion"): (
        "comparison_against_incumbent_complete",
        "operator_explicit_approval",
    ),
}


def gates_for(from_status: str, to_status: str) -> tuple[str, ...]:
    """Return the documented gates for a given transition (empty if none)."""
    return PROMOTION_GATES.get((from_status, to_status), ())
