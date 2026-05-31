"""Integrated strategy+ML simulation harness (SIM) — Phase 1.

Design: ``docs/sprint-plans/ROADMAP-INTEGRATED-SIM-2026-05-30.md``.

Phase 1 = **integrated pipeline replay**: walk historical bars and drive
the SAME live functions the production pipeline uses
(``aggregate_intents`` from ``src/runtime/intents.py``) so strategies are
tested THROUGH the real decision funnel — signal -> intent multiplexer ->
(risk gate) -> fill -> portfolio ledger — instead of each in isolation.

The cardinal rule (see the design doc § 2): SIM is a **driver + bookkeeper**.
It must NOT reimplement intent resolution, sizing, or signal logic. It calls
the live functions with historical inputs. Drift between SIM and live is the
exact failure mode this subsystem exists to prevent, so SIM forking live
logic would defeat its purpose.

Phases 2 (models-in-the-loop), 3 (decision-attrition) and 4 (variation sweep)
build on this engine; they are not in this module yet.

This package is **read-only** against history + config. It writes only to its
own outputs under ``runtime_logs/sim/``. It never touches ``trade_journal.db``,
``config/*``, or the model registry.
"""
from __future__ import annotations

__all__ = [
    "FunnelStage",
    "SimTrade",
    "SimLedger",
    "BarFillModel",
    "run_replay",
    "AccountConfig",
    "SimAccount",
    "ModelScorer",
    "feature_row_for_trade",
    "compute_attrition",
    "eval_n_from_registry",
    "run_sweep",
    "write_sweep",
]

from sim.account import AccountConfig, SimAccount
from sim.ledger import FunnelStage, SimLedger, SimTrade
from sim.fills import BarFillModel
from sim.engine import run_replay
from sim.models import ModelScorer, feature_row_for_trade
from sim.attrition import compute_attrition, eval_n_from_registry
from sim.sweep import run_sweep, write_sweep
