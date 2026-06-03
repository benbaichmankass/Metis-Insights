"""Labeling primitives for ML datasets (S-MLOPT-S5, M14 Phase 1.1).

de Prado (Advances in Financial ML, Ch. 2–3) event sampling + triple-barrier
labeling, as pure stdlib functions so they unit-test without numpy/pandas and
compose into dataset families (`setup_candidates`) and, later, the meta-labeling
model (S-MLOPT-S6).
"""
from .triple_barrier import (
    BarrierConfig,
    BarrierOutcome,
    cusum_events,
    label_event,
)

__all__ = [
    "BarrierConfig",
    "BarrierOutcome",
    "cusum_events",
    "label_event",
]
