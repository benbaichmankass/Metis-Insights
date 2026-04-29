"""Tests for S-007 #115: safe model loader in strategies/breakout_confirmation.py."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers to import _local_model_path without triggering pandas/joblib imports
# ---------------------------------------------------------------------------

def _get_local_model_path_fn():
    """Import _local_model_path from breakout_confirmation without pandas/joblib."""
    # Stub heavy deps so the module-level imports don't blow up.
    for mod in ("pandas", "numpy", "joblib"):
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()
    import importlib
    import strategies.breakout_confirmation as bc
    importlib.reload(bc)
    return bc._local_model_path, bc._LEGACY_LOCAL_MODEL


# ---------------------------------------------------------------------------
# _local_model_path — registry path returned when registry works
# ---------------------------------------------------------------------------

def test_local_model_path_uses_registry(monkeypatch):
    """When registry is available, _local_model_path returns the registry path."""
    fake_reg = types.ModuleType("src.strategy_registry")
    fake_reg.model_path = lambda name: "/repo/models/btc_v1.joblib"
    monkeypatch.setitem(sys.modules, "src.strategy_registry", fake_reg)

    fn, _ = _get_local_model_path_fn()
    result = fn()
    assert str(result) == "/repo/models/btc_v1.joblib"


def test_local_model_path_falls_back_when_registry_broken(monkeypatch):
    """Falls back to legacy path when registry raises."""
    class _Boom:
        def __getattr__(self, _):
            raise RuntimeError("registry broken")

    monkeypatch.setitem(sys.modules, "src.strategy_registry", _Boom())
    fn, legacy = _get_local_model_path_fn()
    assert fn() == legacy


def test_local_model_path_falls_back_when_registry_returns_none(monkeypatch):
    """Falls back to legacy path when registry.model_path() returns None."""
    fake_reg = types.ModuleType("src.strategy_registry")
    fake_reg.model_path = lambda name: None
    monkeypatch.setitem(sys.modules, "src.strategy_registry", fake_reg)

    fn, legacy = _get_local_model_path_fn()
    assert fn() == legacy


# ---------------------------------------------------------------------------
# _load_model — safe error on missing local file
# ---------------------------------------------------------------------------

def test_load_model_raises_file_not_found_with_clear_message(monkeypatch, tmp_path):
    """When HF Hub fails and the local file doesn't exist, raises FileNotFoundError
    with a helpful message (not a raw joblib OSError)."""
    import importlib

    for mod in ("pandas", "numpy", "joblib"):
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

    # Point registry at a non-existent path.
    fake_reg = types.ModuleType("src.strategy_registry")
    fake_reg.model_path = lambda name: str(tmp_path / "missing.joblib")
    monkeypatch.setitem(sys.modules, "src.strategy_registry", fake_reg)

    # Make HF Hub unavailable.
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)

    import strategies.breakout_confirmation as bc
    importlib.reload(bc)

    import pytest
    with pytest.raises(FileNotFoundError, match="Breakout model not found"):
        bc._load_model()


def test_load_model_uses_local_file_when_hf_unavailable(monkeypatch, tmp_path):
    """When HF Hub is unavailable but the local file exists, loads from it."""
    import importlib

    fake_joblib = MagicMock()
    fake_joblib.load.return_value = "fake_model"
    sys.modules["joblib"] = fake_joblib
    for mod in ("pandas", "numpy"):
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

    # Create a dummy model file.
    model_file = tmp_path / "btc_v1.joblib"
    model_file.write_bytes(b"fake")

    fake_reg = types.ModuleType("src.strategy_registry")
    fake_reg.model_path = lambda name: str(model_file)
    monkeypatch.setitem(sys.modules, "src.strategy_registry", fake_reg)
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)

    import strategies.breakout_confirmation as bc
    importlib.reload(bc)

    result = bc._load_model()
    assert result == "fake_model"
    fake_joblib.load.assert_called_once_with(str(model_file))


# ---------------------------------------------------------------------------
# registry integration — model_path returns absolute path ending in .joblib
# ---------------------------------------------------------------------------

def test_registry_breakout_model_path_is_absolute():
    from src.strategy_registry import model_path
    p = model_path("breakout_confirmation")
    assert p is not None
    assert Path(p).is_absolute()
    assert p.endswith(".joblib")


def test_registry_vwap_model_path_is_none():
    from src.strategy_registry import model_path
    assert model_path("vwap") is None


def test_registry_ict_model_path_is_none():
    from src.strategy_registry import model_path
    assert model_path("ict") is None
