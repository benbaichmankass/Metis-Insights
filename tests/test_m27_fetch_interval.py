"""M27 P1 — tests for the fetch puller's timeframe generalization (--interval)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research" / "m27" / "fetch_bybit_5m.py"


def _load():
    spec = importlib.util.spec_from_file_location("m27_fetch", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_interval_maps_label_to_code_and_bar_ms():
    m = _load()
    assert m.resolve_interval("1m") == ("1", 60_000)
    assert m.resolve_interval("5m") == ("5", 300_000)
    assert m.resolve_interval("15m") == ("15", 900_000)
    assert m.resolve_interval("1h") == ("60", 3_600_000)
    assert m.resolve_interval("4h") == ("240", 14_400_000)


def test_resolve_interval_is_case_and_space_tolerant():
    m = _load()
    assert m.resolve_interval(" 1M ") == ("1", 60_000)


def test_resolve_interval_rejects_unsupported_label():
    m = _load()
    with pytest.raises(ValueError):
        m.resolve_interval("7m")
    with pytest.raises(ValueError):
        m.resolve_interval("nonsense")


def test_default_interval_is_5m_backward_compatible():
    m = _load()
    # the historical default stays 5m → same Bybit code + bar as before
    assert m.resolve_interval("5m") == ("5", 5 * 60 * 1000)
