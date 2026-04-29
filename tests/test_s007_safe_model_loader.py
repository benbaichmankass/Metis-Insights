"""Tests for S-007 #115 registry-side model_path lookups.

S-012 PR C5: the strategies/breakout_confirmation.py loader (and its
huggingface-hub fallback) was deleted along with the breakout strategy.
The registry-side tests survive — they exercise src/strategy_registry.py
directly and don't depend on any deleted module.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# registry integration — model_path returns absolute path or None
# ---------------------------------------------------------------------------


def test_registry_vwap_model_path_is_none():
    from src.strategy_registry import model_path
    assert model_path("vwap") is None


def test_registry_turtle_soup_model_path_is_none():
    """S-012 PR B1: turtle_soup is the new strategy; no model artefact."""
    from src.strategy_registry import model_path
    assert model_path("turtle_soup") is None
