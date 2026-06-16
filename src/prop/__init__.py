"""Prop-firm strategy evaluation tool (Tier-1 research/backtest tooling).

Self-contained unit that judges a portfolio backtest's equity curve +
closed-trade ledger against a prop-firm ruleset (profit target, daily-loss,
max-drawdown, position-size, consistency, funded soak). It NEVER touches the
live order path — it consumes the output of ``scripts/backtest_system.py``'s
portfolio engine and reports pass/fail.

Design: ``docs/research/prop-firm-testing-tool-DESIGN.md``.

Public surface:
  - :mod:`src.prop.ruleset`   — load + validate a ruleset YAML into a dataclass.
  - :mod:`src.prop.evaluator` — the six checks over an equity curve + ledger.
  - :mod:`src.prop.report`    — verdict list → Markdown matrix + JSON.
"""
from __future__ import annotations

from src.prop.evaluator import EquityPoint, TradeRecord, evaluate
from src.prop.ruleset import PropRuleset, load_ruleset

__all__ = [
    "PropRuleset",
    "load_ruleset",
    "EquityPoint",
    "TradeRecord",
    "evaluate",
]
