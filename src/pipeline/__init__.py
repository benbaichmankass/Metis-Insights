"""Canonical pipeline types and stage contracts.

Durable artifact of WS2 in `docs/AI-TRADERS-ROADMAP.md`. Establishes
typed schemas and stage contracts that the live runtime path
(`src/runtime/pipeline.py` and `src/units/`) will gradually adopt.
WS2 lands the types; later sprints migrate runtime call sites onto
them without changing live behavior.

See:
  - `docs/architecture/ai-model-platform.md` for the layer map.
  - `docs/pipeline/stage-contracts.md` for the per-stage contract spec.

Safety invariant: any model output flowing through these types must
remain rejectable by deterministic risk gating
(`src/units/accounts/risk.py`) and broker validation
(`src/runtime/orders.py`). Schemas in this module make the rejection
hook explicit but do not loosen any existing live check.
"""
from __future__ import annotations

from .types import (
    DecisionVerdict,
    Direction,
    ExecutionIntent,
    RejectionSource,
    StageDecision,
    StageName,
    TradeCandidate,
)

__all__ = [
    "DecisionVerdict",
    "Direction",
    "ExecutionIntent",
    "RejectionSource",
    "StageDecision",
    "StageName",
    "TradeCandidate",
]
