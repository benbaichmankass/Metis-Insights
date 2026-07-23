"""M29 — AI-Driven System-Dynamics Modelling (Layer 1: Signals / Modelling).

A pure stock-flow simulation engine + a causal-structure descriptor + a
system-identification harness + the P1 seed model (EIA weekly NG storage → MNG
price). Feeds M28's macro/value sleeve (the eventual ``c_macro`` overlay is Tier-3,
backtest-gated) and generalises to the fleet self-model (target B, P6).

**Purity is a contract** (`.importlinter` locks it): nothing here imports the
Execution layer, a broker adapter, or the order path. No I/O, no randomness, no
clock — data is injected by the caller. Design of record:
``docs/research/M29-ai-system-dynamics-DESIGN.md``.
"""

from __future__ import annotations

from .engine import Flow, Model, Trajectory, simulate
from .identify import (
    FitResult,
    StabilityReport,
    identify,
    r_squared,
    rmse,
    sse,
    walk_forward_stability,
)
from .structure import CausalStructure, Link, Loop

__all__ = [
    "Flow",
    "Model",
    "Trajectory",
    "simulate",
    "CausalStructure",
    "Link",
    "Loop",
    "identify",
    "walk_forward_stability",
    "FitResult",
    "StabilityReport",
    "sse",
    "rmse",
    "r_squared",
]
